import os,sys,json,time,subprocess,array,uuid
from pathlib import Path
root=Path(__file__).resolve().parents[3]
run=uuid.uuid4().hex[:12]
os.environ['LONGVIDEO_DATA']=str(root/'work/integration-data'/run)
os.environ['LONGVIDEO_EXPORTS']=str(root/'work/integration-exports'/run)
os.environ['LONGVIDEO_TRANSCRIPTS']=str(root/'work/integration-transcripts'/run)
sys.path.insert(0,str(root/'outputs/longvideo-editor'))
from media import ffmpeg,probe,render,binaries
from domain import make_item
from fastapi.testclient import TestClient
from app import app
import app as application
from storage import get,save
# This local export check must never submit an automatic cloud transcription.
application.get_key=lambda: ''
H={'X-Longvideo-Client':'1'}
folder=Path(os.environ['LONGVIDEO_DATA']);fixture=folder/'fixture.mp4'
ffmpeg(['-f','lavfi','-i','color=c=red:s=320x180:r=25:d=4',
        '-f','lavfi','-i','color=c=blue:s=320x180:r=25:d=4',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=4',
        '-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=4',
        '-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a][3:a]concat=n=2:v=0:a=1[a]',
        '-map','[v]','-map','[a]','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',fixture],8)
with TestClient(app,base_url='http://127.0.0.1') as c:
    assert c.post('/api/import',json={'path':str(fixture)}).status_code==403
    imported=c.post('/api/import',json={'path':str(fixture)},headers=H)
    assert imported.status_code==200,imported.text
    p=imported.json()[0]
    # Explicit synthetic captions; these never enter the user's projects.
    sentences=[{'id':f'fixture:{i}','source_index':i,'version':'synthetic-fixture',
                'text':text,'start_ms':start,'end_ms':end,'words':[]}
               for i,(text,start,end) in enumerate([('红画面低音',1000,2500),('蓝画面高音',5000,6500)])]
    p.update(sentences=sentences)
    save('project',p)
    group={'id':'test-group','name':'本地测试:蓝色先/红色后','items':[make_item(sentences[1]),make_item(sentences[0])]}
    assert c.put(f"/api/projects/{p['id']}/groups",headers=H,json={'groups':[group]}).status_code==200
    destination=folder/'自定义导出'
    saved=c.put('/api/settings',headers=H,json={'exports_path':str(destination)})
    assert saved.status_code==200,saved.text
    assert Path(c.get('/api/info').json()['exports_path'])==destination.resolve()
    bad={**group,'items':[{**group['items'][0],'end_ms':9000}]}
    assert c.put(f"/api/projects/{p['id']}/groups",headers=H,json={'groups':[bad]}).status_code==400
    plan=c.post(f"/api/projects/{p['id']}/export-plan",headers=H,json={}).json()
    assert plan[0]['file_name'].startswith('本地测试_蓝色先_红色后'),plan
    assert c.get(f"/api/projects/{p['id']}/media",headers={'Range':'bytes=0-99'}).status_code==206
    j=c.post(f"/api/projects/{p['id']}/export",headers=H,json={}).json()
    for _ in range(100):
        job=next(x for x in c.get('/api/jobs').json() if x['id']==j['id'])
        if job['status'] not in ('running','queued'):break
        time.sleep(.2)
    assert job['status']=='succeeded',job
    result=Path(job['results'][0]['path']);meta=probe(result)
    assert result.parent==destination.resolve(),result
    assert list(destination.iterdir())==[result]
    assert abs(meta['duration_ms']-plan[0]['duration_ms'])<100,(meta['duration_ms'],plan)
    def output(args):
        return subprocess.check_output([binaries()['ffmpeg'],'-v','error',*map(str,args)],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    colors=[];frequencies=[]
    for seconds in (.5,2.3):
        rgb=output(['-ss',seconds,'-i',result,'-frames:v','1','-vf','scale=1:1','-pix_fmt','rgb24','-f','rawvideo','pipe:1'])
        colors.append(list(rgb))
        pcm=output(['-ss',seconds,'-i',result,'-t','0.4','-vn','-ar','48000','-ac','1','-f','s16le','pipe:1'])
        samples=array.array('h',pcm)
        crossings=sum(a<=0<b for a,b in zip(samples,samples[1:]))
        frequencies.append(crossings/(len(samples)/48000))
    assert colors[0][2]>colors[0][0]+150 and colors[1][0]>colors[1][2]+150,colors
    assert abs(frequencies[0]-880)<10 and abs(frequencies[1]-440)<10,frequencies
    assert c.get('/api/exports/'+result.name).status_code==200
    report={'status':'passed','checks':['origin guard','import','group persistence','custom export folder persistence','only MP4 in custom folder','invalid bounds rejected','filename normalization','video Range request','background export','duration','reordered decoded colors','reordered audio frequencies','file download'], 'output_ms':meta['duration_ms'],'planned_ms':plan[0]['duration_ms'],'decoded_colors':colors,'decoded_frequencies':frequencies}
    (root/'work/local-integration-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False))

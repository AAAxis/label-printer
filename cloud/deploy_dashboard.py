from pathlib import Path
import json,sys,base64
from urllib.request import Request,urlopen
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from printer import load_key
root=Path('dashboard');files=['package.json','package-lock.json','index.html','vercel.json','src/main.jsx','src/style.css','api/data.js','api/session.js','api/queue-data.js','api/db.js','api/agent.js']
payload={'name':'montigate-print-desk','project':(root/'.project-id').read_text(),'target':'production','files':[{'file':f,'data':(root/f).read_text()} for f in files],'projectSettings':{'framework':'vite','buildCommand':'npm run build','outputDirectory':'dist'}}
payload['files'] += [{'file':f.relative_to(root).as_posix(),'data':base64.b64encode(f.read_bytes()).decode(),'encoding':'base64'} for f in (root/'public').rglob('*') if f.is_file()]
r=Request('https://api.vercel.com/v13/deployments?teamId=team_8MJZ7s4aAXoP6b074ayIMzBJ',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+load_key('VERCEL_TOKEN'),'Content-Type':'application/json'})
with urlopen(r,timeout=30) as response:d=json.load(response)
(root/'.deployment.json').write_text(json.dumps({'id':d['id'],'url':d['url']}))
print(json.dumps({'id':d['id'],'url':d['url'],'status':d.get('readyState')}))

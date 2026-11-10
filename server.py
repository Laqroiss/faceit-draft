from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import json
import urllib.error
import urllib.parse
import urllib.request
import time

ROOT = __import__('pathlib').Path(__file__).parent

class Handler(BaseHTTPRequestHandler):
    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        path = ROOT / ('index.html' if urlparse(self.path).path == '/' else urlparse(self.path).path.lstrip('/'))
        if not path.is_file() or ROOT not in path.resolve().parents:
            self.send_error(404); return
        body = path.read_bytes(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        if urlparse(self.path).path != '/api/fetch': self.send_error(404); return
        try:
            req = json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
            token = req.get('api_key','').strip(); names = req.get('players',[]); game = req.get('game','cs2'); limit = max(1,min(1000,int(req.get('match_limit',300))))
            if not token: raise ValueError('Укажите FACEIT API key.')
            if not names or len(names)>80: raise ValueError('Добавьте от 1 до 80 участников.')
            headers={'Authorization':'Bearer '+token,'Accept':'application/json'}
            def get(url):
                for attempt in range(4):
                    r=urllib.request.Request(url,headers=headers)
                    try:
                        with urllib.request.urlopen(r,timeout=25) as x: return json.loads(x.read())
                    except urllib.error.HTTPError as e:
                        if e.code==429 and attempt<3:
                            time.sleep(2**attempt)
                            continue
                        if e.code==404: raise LookupError('FACEIT вернул 404.')
                        msg={401:'Неверный или отозванный API key.',403:'FACEIT запретил доступ к этому ресурсу.',429:'FACEIT продолжает ограничивать запросы. Попробуйте меньше игроков или повторите позже.'}.get(e.code,'Ошибка FACEIT API ('+str(e.code)+').')
                        raise ValueError(msg)
            out=[]; skipped=[]; stats_missing=[]
            for nickname in names:
                try:
                    p=get('https://open.faceit.com/data/v4/players?nickname='+urllib.parse.quote(nickname))
                except LookupError:
                    skipped.append(nickname+' (профиль не найден)')
                    continue
                game_data=p.get('games',{}).get(game,{})
                if not game_data:
                    skipped.append(nickname+' (нет профиля '+game.upper()+')')
                    continue
                pid=p['player_id']; stats=[]
                def walk(obj):
                    if isinstance(obj,dict):
                        for k,v in obj.items():
                            yield str(k),v
                            yield from walk(v)
                    elif isinstance(obj,list):
                        for v in obj: yield from walk(v)
                def rating_value(obj):
                    values=[]; found=set()
                    for key,value in walk(obj):
                        normalized=''.join(c for c in key.lower() if c.isalnum())
                        looks_like_rating='rating' in normalized and not any(x in normalized for x in ('count','change','deviation','delta'))
                        if looks_like_rating and 'hltv' not in normalized:
                            found.add(key)
                            try: values.append(float(str(value).replace('%','')))
                            except (ValueError,TypeError): pass
                    return (sum(values)/len(values) if values else None),found
                # FACEIT exposes match-level player stats in pages of up to 100.
                # Its documented stats offset currently tops out at 200.
                for offset in range(0,min(limit,300),100):
                    try:
                        page=get(f'https://open.faceit.com/data/v4/players/{pid}/games/{game}/stats?offset={offset}&limit=100')
                    except LookupError:
                        stats_missing.append(p.get('nickname',nickname))
                        break
                    items=page.get('items',[]); stats.extend(items)
                    if len(items)<100: break
                def flat(item): return item.get('stats',item) if isinstance(item,dict) else {}
                records=[]
                rating_fields=set()
                for item in stats:
                    s=flat(item)
                    fields=list(walk(s))
                    rating_fields.update(k for k,_ in fields if 'rating' in k.lower())
                    def pick(*keys):
                        wanted={''.join(c for c in a.lower() if c.isalnum()) for a in keys}
                        for k,v in fields:
                            normalized=''.join(c for c in k.lower() if c.isalnum())
                            if normalized in wanted and 'hltv' not in normalized:
                                try:return float(str(v).replace('%',''))
                                except (ValueError,TypeError): pass
                        return None
                    match_rating,match_rating_fields=rating_value(s)
                    rating_fields.update(match_rating_fields)
                    records.append({'rating':match_rating,'elo_change':pick('Elo','Elo change'),'kills':pick('Kills'),'deaths':pick('Deaths'),'wins':pick('Result')})
                lifetime={}
                try:
                    lifetime=get(f'https://open.faceit.com/data/v4/players/{pid}/stats/{game}')
                except LookupError:
                    pass
                lifetime_rating,lifetime_fields=rating_value(lifetime)
                rating_fields.update(lifetime_fields)
                def mean(key):
                    a=[r[key] for r in records if r[key] is not None]
                    return sum(a)/len(a) if a else None
                match_rating=mean('rating')
                out.append({'nickname':p.get('nickname',nickname),'player_id':pid,'level':game_data.get('skill_level',0),'elo':game_data.get('faceit_elo',0),'country':p.get('country',''),'matches':len(records),'rating_avg':match_rating if match_rating is not None else lifetime_rating,'rating_source':'matches' if match_rating is not None else ('profile lifetime' if lifetime_rating is not None else None),'rating_count':sum(r['rating'] is not None for r in records),'rating_fields':sorted(rating_fields),'kills_avg':mean('kills'),'deaths_avg':mean('deaths'),'records':records})
            if not out: raise ValueError('Не удалось загрузить ни одного CS2 профиля. Проверьте написание ников и выбранную игру.')
            self._json(200,{'players':out,'skipped':skipped,'stats_missing':stats_missing,'note':'FACEIT match stats are returned only within API availability and documented pagination. Import a full match export as CSV to extend coverage.'})
        except Exception as e: self._json(400,{'error':str(e)})
    def log_message(self,*args): pass

if __name__ == '__main__':
    print('Откройте http://127.0.0.1:8765')
    ThreadingHTTPServer(('127.0.0.1',8765),Handler).serve_forever()

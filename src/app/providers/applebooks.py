"""Apple's public search API and public product-page recording details."""
import re
import urllib.parse
from html.parser import HTMLParser
from app.providers.base import Http, Provider, candidate


class Text(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'): self.skip += 1
    def handle_endtag(self, tag):
        if tag in ('script','style') and self.skip: self.skip -= 1
    def handle_data(self, value):
        if not self.skip and value.strip(): self.parts.append(value.strip())


class AppleBooks(Provider):
    name='applebooks'; label='Apple Books'; site='https://books.apple.com'; matching=False; min_interval=3.1
    def __init__(self): self.http=Http(self.label,self.min_interval)
    def search(self,title,author=''):
        url='https://itunes.apple.com/search?'+urllib.parse.urlencode(dict(term=f'{title} {author}',media='audiobook',entity='audiobook',country='GB',limit=8))
        raw=self.cache('search:'+title+':'+author,lambda:self.http.request(url)) or {}
        return [candidate(self.name,id=str(c.get('collectionId','')),title=c.get('collectionName',''),authors=[c.get('artistName','')],release_date=c.get('releaseDate',''),description=c.get('description',''),url=c.get('collectionViewUrl',''),cover_url=c.get('artworkUrl100','')) for c in raw.get('results',[]) if c.get('wrapperType')=='audiobook']
    def details(self,c):
        url=c.get('url',''); parsed=urllib.parse.urlparse(url)
        if parsed.scheme!='https' or parsed.hostname!='books.apple.com': raise ValueError('invalid Apple Books URL')
        raw=self.cache('page:'+c['id'],lambda:self.http.request(url,text=True)); parser=Text();parser.feed(raw or ''); lines=parser.parts; out=dict(c)
        def section(start,end):
            if start not in lines: return []
            i=lines.index(start)+1
            return lines[i:lines.index(end,i)] if end in lines[i:] else []
        narr=section('NARRATOR','LANGUAGE'); dur=section('LENGTH','RELEASED'); lang=section('LANGUAGE','LENGTH')
        if narr: out['narrators']=[narr[-1]]
        if lang: out['language']=lang[-1].lower()
        m=re.search(r'(\d+):(\d+)', ' '.join(dur))
        if m: out['runtime_minutes']=int(m[1])*60+int(m[2])
        if '(Unabridged)' in c['title']: out['abridged']=False
        return out

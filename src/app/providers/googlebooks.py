"""Optional Google Books work metadata; keys travel in headers, not archived URLs."""
import os
import urllib.parse
from app.providers.base import Http, Provider, candidate


class GoogleBooks(Provider):
    name='googlebooks';label='Google Books';site='https://books.google.com';matching=False;min_interval=1.1
    def __init__(self): self.http=Http(self.label,self.min_interval)
    def unavailable(self): return None if os.environ.get('APP_GOOGLE_BOOKS_KEY') else 'set APP_GOOGLE_BOOKS_KEY'
    def search(self,title,author=''):
        url='https://www.googleapis.com/books/v1/volumes?'+urllib.parse.urlencode(dict(q=f'intitle:{title} inauthor:{author}',maxResults=8))
        raw=self.cache('search:'+title+':'+author,lambda:self.http.request(url,headers={'X-Goog-Api-Key':os.environ.get('APP_GOOGLE_BOOKS_KEY','')})) or {}
        out=[]
        for item in raw.get('items',[]):
            v=item.get('volumeInfo') or {};out.append(candidate(self.name,id=item.get('id',''),title=v.get('title',''),authors=v.get('authors') or [],description=v.get('description',''),rating=v.get('averageRating'),ratings_count=v.get('ratingsCount'),genres=v.get('categories') or [],url=v.get('infoLink',''),isbns=[i['identifier'] for i in v.get('industryIdentifiers',[])],release_date=v.get('publishedDate','')))
        return out

"""Book boundaries follow file evidence, including mixed flat and nested collections."""
import pytest
from app.ingest import grouping as G,inventory as I


def file(path,album='',author='',**tags):
    return dict(path=path,size=10,probe=dict(duration=60,tags=dict(album=album,artist=author,**tags)))


@pytest.mark.parametrize('value,wanted',[('001 American Gods 10th Anniversary','American Gods 10th Anniversary'),('Dune-Part02','Dune'),('Track 02',''),('01',''),('A Book (Unabridged)','A Book'),('BBC R4X - Novel, Episode 1','BBC R4X - Novel'),('Unknown album',''),('1984','1984'),('2001','2001'),('0001',''),('Unabridged 1','')])
def test_group_title_hints_keep_book_words_and_remove_sequence_labels(value,wanted):
    assert G.title_hint(value)==wanted


def test_grouping_splits_books_in_one_folder_and_unites_the_same_book_across_folders():
    files=[file('/books/A-Part01.mp3','A','Writer'),file('/books/A-Part02.mp3','A','Writer'),file('/books/B-Part01.mp3','B','Writer'),file('/books/other-copy/01.mp3','A','Writer')]
    groups=G.partition(files,'/books')
    assert [len(g['paths']) for g in groups]==[3,1]
    assert groups[0]['paths']==['/books/A-Part01.mp3','/books/A-Part02.mp3','/books/other-copy/01.mp3']
    assert [g['title'] for g in groups]==['A','B']


def test_grouping_uses_filenames_for_collection_tags_and_keeps_distinct_authors():
    files=[file('/books/A-Part01.mp3','Complete works'),file('/books/A-Part02.mp3','Complete works'),file('/books/B-Part01.mp3','Complete works'),file('/books/B-Part02.mp3','Complete works')]
    assert [g['title'] for g in G.partition(files,'/books')]==['A','B']
    assert len(G.partition([file('/books/1.mp3','Same Title','Frank Herbert'),file('/books/2.mp3','Same Title','Another Writer')],'/books'))==2
    assert len(G.partition([file('/books/1.mp3','Same Title','Frank Herbert'),file('/books/2.mp3','Same Title','F. Herbert')],'/books'))==1


def test_disc_folders_are_sequence_clues_and_not_alternative_recordings():
    files=[file('/book/CD1/01.mp3','Novel','Writer',track='1/2'),file('/book/CD1/02.mp3','Novel','Writer',track='2/2'),file('/book/CD2/01.mp3','Novel','Writer',track='1/1')]
    assert len(G.partition(files,'/book'))==1
    representations=I.represent(files)
    assert len(representations)==1 and representations[0]['duration']==180
    assert representations[0]['missing_tracks']==0 and representations[0]['duplicate_tracks']==0
    files[0]['probe']['tags']['disc']='1/3'
    assert I.represent(files)[0]['missing_tracks']==1


def test_part_filename_gaps_remain_visible_after_work_splitting():
    rep=I.represent([file('/book/Novel-Part01.mp3'),file('/book/Novel-Part03.mp3')])[0]
    assert rep['missing_tracks']==1


def test_untagged_named_parts_share_work_author_and_declared_sequence_gaps():
    files=[file(f"/John Mortimer Summer's Lease/John Mortimer - Summer's Lease {n:02} of 16.mp3") for n in range(1,17)]
    [group]=G.partition(files,"/John Mortimer Summer's Lease")
    assert (group['title'],group['author'],len(group['paths']))==("Summer's Lease",'John Mortimer',16)
    [rep]=I.represent(files)
    assert rep['files']==[f['path'] for f in files] and rep['duration']==960 and rep['missing_tracks']==0
    # Both leading and trailing omissions remain visible from the declared total.
    [rep]=I.represent(files[1:-1])
    assert rep['missing_tracks']==2 and len(rep['files'])==14


def test_numeric_copy_counter_chapters_keep_the_preview_as_an_audio_alternative():
    files=[file(f'/Reborn.on.the.run/1 ({n}).mp3') for n in range(1,24)]
    preview=file('/Reborn.on.the.run/Reborn on the Run by Catra Corbett, Dan England, and Allyson Ryan - Listen Online.mp3')
    preview['probe']['duration']=5;files.append(preview)
    [group]=G.partition(files,'/Reborn.on.the.run')
    assert len(group['paths'])==24 and G.partition(files,'/Reborn.on.the.run')==[group]
    reps=I.represent(files)
    assert sorted(len(r['files']) for r in reps)==[1,23]
    chapters=next(r for r in reps if len(r['files'])==23)
    assert chapters['files']==[f['path'] for f in files[:-1]] and chapters['missing_tracks']==0
    assert next(r for r in reps if len(r['files'])==1)['files']==[preview['path']]
    del files[9]
    assert next(r for r in I.represent(files) if len(r['files'])==22)['missing_tracks']==1


def test_bare_numbered_mp3_files_require_fixed_size_chunk_evidence():
    files=[file(f'/Ego Is the Enemy/Ego Is the Enemy {n}.mp3') for n in range(1,6)]
    for f,size in zip(files,[99000,100000,100000,100000,80000]):f['size']=size
    [group]=G.partition(files,'/Ego Is the Enemy')
    assert (group['title'],len(group['paths']))==('Ego Is the Enemy',5)
    [rep]=I.represent(files)
    assert rep['files']==[f['path'] for f in files] and rep['missing_tracks']==0
    # Unequal chapter sizes cannot establish that bare suffix numbers are parts.
    files[2]['size']=95000
    assert len(G.partition(files,'/Ego Is the Enemy'))==5
    assert len(I.represent(files))==5


@pytest.mark.parametrize('names',[
    ['Novel 1 of 3.mp3','Novel 2 of 4.mp3'],
    ['Novel 01 of 3.mp3','Novel 1 of 3.mp3','Novel 2 of 3.mp3'],
    ['1 (1).mp3','1 (01).mp3','1 (2).mp3'],
    ['1 (2).mp3','1 (3).mp3','1 (4).mp3'],
    ['Stories Volume 1.mp3','Stories Volume 2.mp3','Stories Volume 3.mp3','Stories Volume 4.mp3'],
    ['Stories Volume 1 of 3.mp3','Stories Volume 2 of 3.mp3','Stories Volume 3 of 3.mp3'],
])
def test_ambiguous_numbering_does_not_establish_a_filename_sequence(names):
    files=[file('/Novel/'+name) for name in names]
    assert G.filename_layout(files)=={}


def test_filename_sequences_respect_tagged_work_boundaries_and_generic_containers():
    files=[file(f'/mixed collection/1 ({n}).mp3') for n in (1,2,3)]
    assert G.filename_layout(files)=={}
    files=[file(f'/Novel/Novel {n:02} of 3.mp3','Another Work') for n in (1,2,3)]
    assert G.filename_layout(files)=={}
    # Similar file sizes alone do not combine independently numbered volumes.
    files=[file(f'/Stories/Stories Volume {n}.mp3') for n in (1,2,3,4)]
    for f,size in zip(files,[99000,100000,100000,80000]):f['size']=size
    assert G.filename_layout(files)=={} and len(G.partition(files,'/Stories'))==4


def test_explicit_part_word_before_the_declared_total_is_packaging_not_work_title():
    files=[file(f'/Novel/Novel Part {n:02} of 3.mp3') for n in (1,2,3)]
    [group]=G.partition(files,'/Novel');[rep]=I.represent(files)
    assert group['title']=='Novel' and rep['missing_tracks']==0


def test_copy_counters_do_not_concatenate_whole_book_formats_or_identical_downloads():
    files=[file(f'/Novel/1 ({n}).m4b') for n in (1,2,3)]
    assert G.filename_layout(files)=={} and len(I.represent(files))==3
    files=[dict(file(f'/Novel/1 ({n}).mp3'),sha256='same recording') for n in (1,2,3)]
    assert G.filename_layout(files)=={} and len(I.represent(files))==3


def test_coded_chapters_share_a_work_and_remain_a_separate_audio_alternative():
    files=[file('/American Psycho/01.m4b','American Psycho','Bret Easton Ellis',track='1/2'),file('/American Psycho/02.m4b','American Psycho','Bret Easton Ellis',track='2/2')]
    files += [file('/American Psycho/'+name) for name in ('APS000 Dedication.mp3','APS301 April Fools.mp3','APS302 Morning.mp3','APS303 At Lunch.mp3')]
    [group]=G.partition(files,'/American Psycho')
    assert group['title']=='American Psycho' and len(group['paths'])==6
    reps=I.represent(files)
    assert sorted(len(r['files']) for r in reps)==[2,4]
    assert all(r['missing_tracks']==0 and r['duplicate_tracks']==0 for r in reps)
    assert next(r for r in reps if len(r['files'])==4)['files'][-1].endswith('APS303 At Lunch.mp3')
    files=[f for f in files if 'APS302' not in f['path']]
    assert next(r for r in I.represent(files) if len(r['files'])==3)['missing_tracks']==1


def test_different_codes_and_nonsequential_titles_do_not_collapse_into_one_work():
    files=[file(f'/mixed/{code}{n:03} Chapter.mp3') for code in ('APS','LTZ') for n in (1,2,3)]
    assert len(G.partition(files,'/mixed'))==2
    files=[file(f'/mixed/ABC{n} Different Title.mp3') for n in (101,201,301)]
    assert not G.coded_parts(f['path'] for f in files)


@pytest.mark.parametrize('album,title,disc',[
    ('Stories Vol Five CD1','Stories 5',1),('Stories VOLUME FIVE CD2','Stories 5',2),
    ('Stories 05 [Disc 3]','Stories 5',3),('Stories Vol.4 CD2','Stories 4',2),
    ('Stories 10 cd 2 A Subtitle','Stories 10',2),('Unknown Album','',0),
])
def test_album_packaging_preserves_volumes_and_normalises_disc_variants(album,title,disc):
    assert G.album_parts(album)[:2]==(title,disc)


def test_story_parts_remain_chapters_when_the_album_has_one_ordered_track_run():
    files=[file(f'/books/Anthology/{n:02} - {story} Part {part}.mp3','Stories','Writer',track=str(n)) for n,story,part in [(1,'One',1),(2,'One',2),(3,'Two',1),(4,'Two',2)]]
    [group]=G.partition(files,'/books')
    assert group['title']=='Stories' and len(group['paths'])==4 and not group['conflicting_album']
    [rep]=I.represent(files)
    assert len(rep['files'])==4 and rep['duplicate_tracks']==0


def test_numbered_sibling_folders_form_discs_of_the_same_volume_with_gaps_visible():
    files=[file(f'/set/Stories {folder:02}/{n:02} - Story {n}.mp3',album,'Writer',track=f'{n}/3') for folder,album in [(5,'Stories Vol Five CD1'),(7,'Stories VOLUME FIVE CD3')] for n in (1,2,3)]
    [group]=G.partition(files,'/set')
    assert group['title']=='Stories 5' and len(group['paths'])==6
    [rep]=I.represent(files)
    assert rep['missing_tracks']==1 and rep['duplicate_tracks']==0 and len(rep['files'])==6
    # A distinct numbered volume stays separate even with identical track names.
    files += [file(f'/set/Stories 08/{n:02} - Story {n}.mp3','Stories Vol Six CD1','Writer',track=str(n)) for n in (1,2,3)]
    assert [g['title'] for g in G.partition(files,'/set')]==['Stories 5','Stories 6']


def test_disc_structure_can_group_inconsistent_artist_tags_but_identity_still_requires_review():
    files=[file(f'/set/Stories {folder}/{n:02} Story {n}.mp3',album,author,track=str(n)) for folder,album,author in [(21,'Stories 8','Wrong Artist'),(22,'Stories 8','Reader'),(23,'Stories 08 [Disc 3]','Writer')] for n in (1,2,3)]
    [group]=G.partition(files,'/set')
    inv=dict(root='/set',files=files,work_hint=group,sidecars=[])
    _,metrics=I.recover(dict(title='',author='',narrator='',asin='',locked=[]),inv)
    assert len(group['paths'])==9 and metrics['identity.conflicts']==1
    [rep]=I.represent(files)
    assert rep['missing_tracks']==0 and rep['duplicate_tracks']==0


def test_same_title_different_authors_and_alternative_recordings_are_preserved():
    files=[file(f'/{author}/Book/{n:02} Different Chapter.mp3','Same Title',author,track=str(n)) for author in ('First Writer','Other Writer') for n in (1,2,3)]
    assert len(G.partition(files,'/'))==2
    files=[file(f'/copies/{copy}/CD1/{n:02}.mp3','Same Title','Writer',track=str(n)) for copy in ('old','new') for n in (1,2,3)]
    assert len(G.partition(files,'/copies'))==1 and len(I.represent(files))==2


def test_track_number_resets_inside_a_folder_do_not_hide_distinct_book_stems():
    files=[file(f'/mixed/{title}-Part{n:02}.mp3','Wrong Album','Writer',track=str(n)) for title in ('Alpha','Beta') for n in (1,2,3)]
    assert [g['title'] for g in G.partition(files,'/mixed')]==['Alpha','Beta']


def test_missing_tags_require_an_explicit_evidence_backed_layout_correction():
    files=[file(f'/set/Unknown/{n:02} Story {n}.mp3','Unknown Album','Unknown Artist',track=str(n)) for n in (1,2,3)]
    assert all(not g.get('grouping_notes') for g in G.partition(files,'/set'))
    for f in files:f['grouping_hint']=dict(album='Stories 9',disc=3,reason='Publisher contents identify this disc')
    [group]=G.partition(files,'/set')
    assert group['title']=='Stories 9' and group['grouping_notes']==['Publisher contents identify this disc']
    assert all(f['probe']['tags']['album']=='Unknown Album' for f in files)


def test_a_single_track_disc_joins_a_longer_disc_and_missing_first_tracks_stay_flagged():
    files=[file(f'/book/CD1/{n:02} Story {n}.mp3','Stories [Disc 1]','Writer',track=f'{n}/4') for n in (2,3,4)]
    files += [file('/book/CD2/01 Finale.mp3','Stories [Disc 2]','Writer',track='1/1')]
    [group]=G.partition(files,'/book')
    assert group['title']=='Stories' and len(group['paths'])==4
    [rep]=I.represent(files)
    assert len(rep['files'])==4 and rep['missing_tracks']==1 and rep['duplicate_tracks']==0


def test_just_william_395_file_structure_and_verified_tag_repairs():
    # A hermetic reconstruction of every disc/volume variant in the real source.
    discs=[('Just William',15),('W Brown',15),('Just William 2 [Disc 1]',15),('Just William 2 [Disc 2]',15),
           ('Just William 3',12),('Just William 3',9),('Just William 3',9),('JUST WILLIAM VOL4 CD1',15),
           ('JUST WILLIAM VOL.4 CD2',15),('JUST WILLIAM VOL4 CD3',15),('JUST WILLIAM VOL4 CD4',16),
           (' JUST WILLIAM VOL FIVE CD1',20),('JUST WILLIAM VOLUME FIVE CD2',20),('JUST WILLIAM VOLUME FIVE CD3',21),
           ('JUST WILLIAM 6',20),('JUST WILLIAM 6 CD2',20),('JUST WILLIAM 6 CD3',20),
           ('Just William 7 cd 1',20),('Just William 7 cd 2',20),('Just William 7 cd 3',20),
           ('Just William 8',4),('Just William 8',4),('Just William 08 [Disc 3]',4),
           ('Just William 9 CD1',4),('Just William 9 CD2',4),('Unknown Album',4),
           ('Just William 10 cd 1',20),('Just William 10 cd 2 Just William Strikes Again',19)]
    artists={1:'Richmal Crompton',2:'W. Brown',3:'Richmal Crompton',4:'Just William',5:'Just William',6:'Richmal Crompton',7:'Just William',21:'Vikas Swarup',22:'Martin Jarvis',23:'Richmal Crompton, Read By Martin Jarvis'}
    files=[file(f'/collection/Just William {disc:02}/{n:02} Story {(n-1)//3} Part {(n-1)%3+1}.mp3',album,artists.get(disc,'Unknown Artist'),track=str(n)) for disc,(album,count) in enumerate(discs,1) for n in range(1,count+1)]
    for f in files:
        if '/Just William 26/' in f['path']:
            n=f['probe']['tags']['track'];f['path']=f'/collection/Just William 26/{n} Unlabelled Tale {n}.mp3'
    assert len(files)==395 and len(G.partition(files,'/collection'))==12
    # The algorithm cannot guess the W Brown / Unknown Album repairs. These
    # explicit decisions came from publisher story contents, not folder numbers.
    corrections={1:('Just William 1',1),2:('Just William 1',2),26:('Just William 9',3)}
    for f in files:
        disc=int(f['path'].split('/')[-2].split()[-1])
        if disc in corrections:
            album,number=corrections[disc];f['grouping_hint']=dict(album=album,disc=number,reason='Publisher story contents confirm this volume and disc')
    groups=G.partition(files,'/collection')
    assert [len(g['paths']) for g in groups]==[30,30,30,61,61,60,60,12,12,39]
    assert len({p for g in groups for p in g['paths']})==395
    for group in groups:
        [rep]=I.represent([f for f in files if f['path'] in group['paths']])
        assert len(rep['files'])==len(group['paths']) and rep['missing_tracks']==0 and rep['duplicate_tracks']==0


def test_a_named_compilation_is_not_treated_as_an_unsorted_collection_folder():
    files=[file(f'/set/{n:02} - Story {(n-1)//2} Part {(n-1)%2+1}.mp3','Stories: A BBC Radio Collection','Writer',track=str(n)) for n in (1,2,3,4)]
    [group]=G.partition(files,'/set')
    assert group['title']=='Stories: A BBC Radio Collection' and len(group['paths'])==4
    assert len(I.represent(files))==1


@pytest.mark.parametrize('style',['posix','windows'])
def test_sequence_and_coded_file_keys_preserve_the_callers_path_spelling(monkeypatch,style):
    from pathlib import PurePosixPath,PureWindowsPath
    cls=PureWindowsPath if style=='windows' else PurePosixPath
    monkeypatch.setattr(G,'Path',cls)
    files=[file(f'C:/Books/CD{disc}/{n:02} Story {n}.mp3',f'Stories [Disc {disc}]','Writer',track=str(n)) for disc in (1,2) for n in (1,2,3)]
    [group]=G.partition(files,'C:/Books')
    assert group['paths']==[f['path'] for f in files]
    code=[file(f'C:/Books/APS{n:03} Chapter.mp3') for n in (1,2,3)]
    assert set(G.coded_parts(f['path'] for f in code))=={f['path'] for f in code}
    assert len(G.partition(code,'C:/Books'))==1


def test_explicit_reader_album_artist_does_not_split_numbered_chapters_into_books():
    files=[file(f'/books/Writer - Finance/{n:03}-Finance.mp3','Finance: The Bankers','Actual Writer',album_artist='Narrated by Book Reader',composer='Narrated by Book Reader',track=f'{n:03}',title=f'{n:03} Chapter {n}') for n in range(1,31)]
    [group]=G.partition(files,'/books/Writer - Finance')
    assert group['author']=='Actual Writer' and len(group['paths'])==30
    [representation]=I.represent(files)
    assert representation['duration']==1800 and representation['missing_tracks']==0
    identity,metrics=I.recover(dict(title='Finance',author='',narrator='',asin='',locked=[]),dict(root='/books/Writer - Finance',files=files,work_hint=group,sidecars=[]))
    assert identity['book']['author']=='Actual Writer' and identity['book']['narrator']=='Book Reader'
    assert metrics['identity.author_known']==1 and metrics['identity.conflicts']==0
    assert files[0]['probe']['tags']['album_artist']=='Narrated by Book Reader'


@pytest.mark.parametrize('credit',['Narrated by: Book Reader','Read by Book Reader','Book Reader (narrator)','Introduction by Helper'])
def test_auxiliary_album_artist_falls_back_to_actual_writer_and_preserves_distinct_authors(credit):
    files=[file(f'/books/{n:02} Chapter.mp3','Same Book',writer,album_artist=credit,track=str(n)) for n,writer in ((1,'First Writer'),(2,'Other Writer'))]
    groups=G.partition(files,'/books')
    assert {g['author'] for g in groups}=={'First Writer','Other Writer'} and len(groups)==2


def test_reader_only_chapter_tags_share_a_group_without_inventing_an_author():
    files=[file(f'/book/{n:02} Chapter.mp3','A Book','',album_artist='Read by Book Reader',track=str(n)) for n in (1,2,3)]
    [group]=G.partition(files,'/book')
    assert group['author']=='' and len(group['paths'])==3
    identity,metrics=I.recover(dict(title='A Book',author='',narrator='',asin='',locked=[]),dict(root='/book',files=files,work_hint=group,sidecars=[]))
    assert identity['book']['narrator']=='Book Reader' and metrics['identity.author_known']==0


def test_unlabelled_album_artist_retains_its_existing_writer_assertion():
    assert G.author_credit(dict(album_artist='Album Writer',artist='Other Writer'))=='Album Writer'
    assert G.author_credit(dict(artist='Writer, Helper - foreword'))=='Writer, Helper - foreword'

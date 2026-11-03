"""Full, incomplete and author-prefixed titles retain subtitle ambiguity checks."""
import pytest
from app import book_titles as B


def c(title,subtitle='',authors=None):return dict(title=title,subtitle=subtitle,authors=authors or ['Writer'])


@pytest.mark.parametrize('wanted,found,accepted',[
    ('A Very Stable Genius: Testing of America',c('A Very Stable Genius'),True),
    ('History: Ancient World',c('History','Modern World'),False),
    ('History: Ancient World',c('History: Ancient World'),True),
    ('History',c('History','Ancient World'),True),
    ('Chi Running',c('ChiRunning'),True),
    ('Chuck Klosterman IV: A Decade of Curious People',c('IV','A Decade of Curious People',['Chuck Klosterman']),True),
    ('A Decade of Curious People',c('Chuck Klosterman IV','A Decade of Curious People',['Chuck Klosterman']),True),
    ('Chuck Klosterman IV: A Decade of Curious People',c('Chuck Klosterman V','A Decade of Curious People',['Chuck Klosterman']),False),
    ('Different Title: Ancient World',c('History','Ancient World'),False),
])
def test_title_matching_handles_missing_subtitles_spacing_and_author_prefixes(wanted,found,accepted):
    assert (B.analyse(wanted,found,', '.join(found['authors']))['score']>=.85)==accepted


def test_missing_subtitle_is_rejected_when_other_subtitles_disambiguate_books():
    candidates=[c('History'),c('History','Ancient World'),c('History','Modern World')]
    assert B.analyse('History',candidates[0],'Writer',candidates)['score']==0
    assert B.analyse('History: Lost World',candidates[0],'Writer',candidates)['score']==0
    assert B.analyse('History: Ancient World',candidates[1],'Writer',candidates)['score']==1
    assert B.analyse('History: Ancient World',candidates[2],'Writer',candidates)['score']==0


def test_subtitle_only_match_does_not_choose_between_different_volumes():
    candidates=[c('Chuck Klosterman IV','Collected essays',['Chuck Klosterman']),c('Chuck Klosterman V','Collected essays',['Chuck Klosterman'])]
    assert B.analyse('Collected essays',candidates[0],'Chuck Klosterman',candidates)['score']==0


@pytest.mark.parametrize('wanted,found',[('Stories 1','Stories'),('Stories 2','Stories 3'),('1984','1985')])
def test_fuzzy_title_matching_cannot_erase_a_volume_or_title_number(wanted,found):
    assert B.analyse(wanted,c(found),'Writer')['score']==0


def test_volume_label_spelling_does_not_change_the_numbered_work_identity():
    assert B.analyse('Stories 2',c('Stories Volume 2'),'Writer')['score']==1
    assert B.analyse('Stories Volume 2',c('Stories Vol. 3'),'Writer')['score']==0

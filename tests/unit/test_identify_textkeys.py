from app.identify import textkeys as tk


def test_title_key_folds_accents_ampersand_articles_and_labels():
    assert tk.title_key('The Hobbit (Unabridged)') == 'hobbit'
    assert tk.title_key('Pride & Prejudice') == tk.title_key('Pride and Prejudice') == 'pride and prejudice'
    assert tk.title_key("Brontë's Jane Eyre [Audible]") == 'brontes jane eyre'
    assert tk.title_key('The') == 'the'  # a lone article is the title


def test_volumes_are_kept_distinct():
    assert tk.title_key('Dune Volume 2') != tk.title_key('Dune Volume 3')


def test_title_variants_most_specific_first_and_unique():
    assert tk.title_variants('The Hobbit: Or There and Back Again') == ['hobbit or there and back again', 'hobbitorthereandbackagain', 'hobbit']
    assert tk.title_variants('Dune') == ['dune']
    assert tk.title_variants('') == []
    assert tk.title_main('Title - Subtitle') == 'title'


def test_person_key_and_variants():
    assert tk.person_key('Tolkien, J.R.R.') == 'j r r tolkien'
    assert tk.person_key('Dr. Jane Smith') == 'jane smith'
    assert tk.person_key('Smith, Jane and Bob') == 'smith jane and bob'  # a list is not "Last, First"
    assert tk.person_variants('J.R.R. Tolkien') == ['j r r tolkien', 'jrr tolkien', 'j tolkien']
    assert tk.person_variants('Jane Q. Smith') == ['jane q smith', 'jane smith']
    assert tk.person_variants('') == []
    assert tk.surname('Jane Smith') == 'smith' and tk.surname('') == ''


def test_isbn_keys_validate_and_convert():
    assert tk.isbn_keys('0-14-103959-0') == ['0141039590', '9780141039596']
    assert tk.isbn_keys('9780141039596') == ['9780141039596', '0141039590']
    assert tk.isbn_keys('9791234567896') == ['9791234567896']
    assert tk.isbn_keys('0141039591') == [] and tk.isbn_keys('9780141039597') == [] and tk.isbn_keys('abc') == []
    assert tk.isbn_keys('080442957X') == ['080442957X', '9780804429573']


def test_asin_excludes_isbns():
    assert tk.asin('b002v0qk4c') == 'B002V0QK4C'
    assert tk.asin('0141039590') == ''
    assert tk.asin('short') == ''

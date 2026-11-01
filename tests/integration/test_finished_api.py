import json

from app import catalogue


def test_finished_output_and_audio_are_confined_to_recorded_chapters(client, tmp_path):
    book = catalogue.add_book(title='Example', author='Writer')
    output = tmp_path / 'output'
    output.mkdir()
    (output / 'chapter.mp3').write_bytes(b'audio')
    (tmp_path / 'secret.mp3').write_bytes(b'secret')
    metadata = {'book': {'title': 'Example'}, 'metadata': {'source': 'test'}, 'chapters': [{'file': 'chapter.mp3', 'title': 'One', 'duration': 1}, {'file': '../secret.mp3', 'title': 'Invalid', 'duration': 1}]}
    (output / 'metadata.json').write_text(json.dumps(metadata))
    catalogue.set_processed(book['id'], {'status': 'done', 'finished': '2026-01-01', 'out_dir': str(output)})
    assert client.get('/api/finished').json()[0]['title'] == 'Example'
    assert client.get(f"/api/books/{book['id']}/output").json() == metadata
    assert client.get(f"/api/books/{book['id']}/audio/0").content == b'audio'
    assert client.get(f"/api/books/{book['id']}/audio/1").status_code == 404
    assert client.get(f"/api/books/{book['id']}/audio/2").status_code == 404

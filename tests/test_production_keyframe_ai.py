"""LLM keyframe review is advisory and bound to the production's real cast."""
import json
import uuid

import pytest

from test_app import server


def test_keyframe_review_uses_selected_card_names_and_does_not_submit_images(server, monkeypatch):
    module, _client, _fake = server
    production_id, segment_id, character_id = (str(uuid.uuid4()) for _ in range(3))
    production = {
        'id': production_id, 'style_bible': 'Soft watercolor', 'visual_style_custom': '',
        'cards': {'characters': [{'id': character_id, 'name': 'Yuki', 'description': 'Pink bob haircut',
                                  'asset_ids': ['existing-image']}],
                  'wardrobe': [], 'props': [], 'environments': [], 'styles': []},
        'segments': [{'id': segment_id, 'index': 1, 'setting': 'Library', 'story': 'Yuki enters.',
                      'action': 'Yuki picks up a book.', 'image_prompt': 'A library desk.',
                      'keyframe_asset_ids': [],
                      'card_selection': {'characters': ['Yuki'], 'wardrobe': [], 'props': [], 'environments': []}}],
    }
    class Manager:
        def assert_active(self, ident):
            assert ident == production_id
            return production

    class LLM:
        def complete_json(self, _model, _system, user, _schema, **_options):
            payload = json.loads(user)
            selected = payload['clips'][0]['selected_cards']['characters']
            assert selected[0]['name'] == 'Yuki'
            assert selected[0]['description'] == 'Pink bob haircut'
            assert selected[0]['has_reference'] is True
            return {'suggestions': [
                {'segment_id': str(uuid.uuid4()), 'needed': True, 'reason': 'unknown', 'prompt': 'Wrong project'},
                {'segment_id': segment_id, 'needed': True, 'reason': 'Composition', 'prompt': 'Yuki at the desk'},
            ]}

    monkeypatch.setattr(module, 'production_manager', lambda: Manager())
    monkeypatch.setattr(module, 'client', lambda: LLM())
    monkeypatch.setattr(module.RESOURCES, 'run_ai', lambda _model, operation: operation('local-model'))
    monkeypatch.setattr(module, 'asset_manager', lambda: (_ for _ in ()).throw(AssertionError('No image job expected')))

    result = module.production_keyframe_analyse(production_id, {'limit': 24})
    assert result['source'] == 'local_ai'
    assert result['suggestions'] == [{'segment_id': segment_id, 'index': 1,
                                      'reason': 'Composition', 'prompt': 'Yuki at the desk'}]


def test_generated_card_image_attach_requires_matching_completed_job(server, monkeypatch):
    module, _client, _fake = server
    production_id, card_id, run_id = (str(uuid.uuid4()) for _ in range(3))
    asset = {'id': str(uuid.uuid4()), 'media_type': 'image'}
    class Manager:
        called = False
        def assert_active(self, _ident):
            return {'cards': {'characters': [{'id': card_id}]}}
        def attach_generated_card_asset(self, ident, kind, card, value):
            self.called = True
            assert (ident, kind, card, value) == (production_id, 'characters', card_id, asset)
            return {'ok': True}
    class Images:
        def __init__(self):
            self.tag = 'wrong-tag'
        def refresh(self, ident):
            assert ident == run_id
            return {'prompt_tag': self.tag, 'status': 'succeeded', 'asset': asset}
    manager, images = Manager(), Images()
    monkeypatch.setattr(module, 'production_manager', lambda: manager)
    monkeypatch.setattr(module, 'asset_manager', lambda: images)
    with pytest.raises(ValueError, match='not a completed result'):
        module.production_card_generated_image_attach(production_id, 'characters', card_id, {'run_id': run_id})
    assert not manager.called
    images.tag = 'card-' + production_id[:8] + '-' + card_id[:8]
    assert module.production_card_generated_image_attach(production_id, 'characters', card_id,
                                                         {'run_id': run_id}) == {'ok': True}
    assert manager.called

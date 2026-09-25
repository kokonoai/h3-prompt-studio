"""Optional, source-bound storyboard prose for a single H3 clip.

The existing classic and continuity-director compilers remain the default.
This renderer reorganises approved project facts; it does not plan new events.
"""
from __future__ import annotations

import re
from decimal import Decimal

from .dialogue_audio import audible_speech_lock
from .identity_stability import visible_identity_lock
from .scene_contract import ensemble_stability_direction


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _sentence(value):
    value = _text(value)
    return value if not value or value[-1] in '.!?。！？' else value + '.'


def _distinct(values):
    seen = set()
    result = []
    for value in values:
        value = _text(value)
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _brief_series_style(value):
    """Keep global acting direction, not the saved all-character voice roster."""
    value = _text(value)
    if not value:
        return ""
    value = re.sub(r'(?im)^\s*#{1,6}\s*series voice style\s*:?\s*$', '', value)
    value = re.split(r'(?im)^\s*#{1,6}\s*(?:the characters|character voices|voice cards)\b', value, maxsplit=1)[0]
    value = re.split(r'(?m)^\s*[-*]\s+', value, maxsplit=1)[0]
    value = re.sub(r'(?<=[.!?])(?=[A-Z])', ' ', value)
    pieces = re.split(r'(?<=[.!?])\s+', value)
    chosen = []
    for piece in pieces:
        piece = piece.strip()
        if not piece or any(marker in piece.casefold() for marker in
                            ('only include voice cards', 'copy each selected voice card',
                             'never rewrite, shorten', 'the characters should')):
            continue
        if piece.casefold() not in {item.casefold() for item in chosen}:
            chosen.append(piece)
    return ' '.join(chosen)


def _asset_roles(references, active, bound, project):
    assets = {item['id']: item for item in active}
    subjects = [item for item in project.get('subjects', []) if isinstance(item, dict)]
    rows = []
    for ref in references:
        token = ref['token']
        asset = assets.get(ref['asset_id'])
        if not asset or ref.get('source') == 'video_soundtrack':
            continue
        kind = asset.get('semantic_role', 'other')
        holders = [item for item in subjects if asset['id'] in bound.get(item.get('id'), [])]
        if asset.get('reference_overview') is True:
            bindings = [item for item in asset.get('reference_card_bindings', [])
                        if isinstance(item, dict) and _text(item.get('name'))]
            mapped = []
            for item in bindings:
                name = _text(item['name'])
                region = _text(item.get('region'))
                facts = _text(item.get('canonical_description'))
                mapped.append(_sentence('Use ' + (region or 'the assigned region') + ' for ' + name
                                        + ('. ' + facts if facts else '')))
            scope = 'Shared ' + kind + ' overview; each named region is a separate identity, not an extra on-screen copy.'
            if mapped:
                scope += ' ' + ' '.join(mapped)
            rows.append(token + ': ' + scope)
            continue
        if asset.get('media_type') == 'audio':
            speaker = ', '.join(item['name'] for item in holders) or 'its assigned speaker'
            role = ('Uploaded voice audio for ' + speaker
                    + '; primary authority for audible identity, not for the spoken words.')
        elif asset.get('media_type') == 'video':
            role = 'Reference video for the explicitly assigned motion or content; do not import unrelated people or events.'
        elif kind in ('face', 'character') and holders:
            role = 'Exact visible identity reference for ' + ', '.join(item['name'] for item in holders) + '.'
        elif kind == 'wardrobe':
            role = 'Wardrobe reference only; preserve the assigned garment without copying an unrelated wearer.'
        elif kind == 'object':
            role = 'Prop appearance reference for ' + (_text(asset.get('name')) or 'the named object') + '.'
        elif kind == 'background':
            role = 'Environment appearance reference; do not add unlisted principal characters.'
        elif kind in ('style', 'palette'):
            role = 'Visual treatment reference only; transfer palette, texture and rendering, not its depicted cast or props.'
        else:
            role = 'Reference for its assigned visual role only.'
        facts = _distinct(item.get('description', '') for item in holders)
        if not facts:
            facts = [_text(asset.get('description'))]
        detail = ' '.join(_sentence(item) for item in facts if item)
        rows.append(token + ': ' + role + (' ' + detail if detail else ''))
    return '\n\n'.join(rows) if rows else 'No picture or audio reference is conditioned for this clip.'


def _camera_note(camera, action):
    if not isinstance(camera, dict):
        return ''
    framing = _text(camera.get('framing')).lower()
    movement = _text(camera.get('movement')).replace('_', ' ').lower()
    speed = _text(camera.get('speed')).lower()
    action_lc = action.casefold()
    notes = []
    if framing and framing not in action_lc:
        if 'close-up' in action_lc and framing == 'medium':
            notes.append('Let the described close-up settle into a medium composition.')
        elif not any(word in action_lc for word in ('close-up', 'medium shot', 'wide shot', 'long shot')):
            notes.append('Use a ' + framing + ' framing.')
    if movement and movement != 'static' and not any(part in action_lc for part in movement.split()):
        notes.append('Move the camera ' + movement + ((' slowly' if speed == 'slow' else ' at ' + speed + ' speed') if speed else '') + '.')
    return ' '.join(notes)


def _object_notes(shot, names):
    contract = shot.get('scene_contract') or {}
    if not isinstance(contract, dict):
        return ''
    text = []
    for row in contract.get('objects', []):
        if not isinstance(row, dict):
            continue
        label = _text(row.get('name')) or 'The prop'
        count = row.get('count')
        detail = _text(row.get('description'))
        holder = _text(row.get('start'))
        for ident, name in names.items():
            holder = re.sub(r'(?<![\w-])' + re.escape(ident) + r'(?![\w-])',
                            lambda _match: name, holder)
        sentence = (f'Keep exactly {count} physical instance' + ('s' if count != 1 else '')
                    + f' of {label}.') if isinstance(count, int) and count > 0 else f'Keep {label} physically consistent.'
        if detail:
            sentence += ' ' + _sentence(detail)
        if holder:
            sentence += ' Initially ' + holder.rstrip('.') + '.'
        text.append(sentence)
    return ' '.join(text)


def _voice_and_dialogue(project, shots, names, speaker_ids):
    voice = project.get('narrative_voice')
    if not isinstance(voice, dict):
        voice = {}
    language = {'en': 'English', 'ja': 'Japanese', 'zh-CN': 'Simplified Chinese',
                'zh-TW': 'Traditional Chinese'}.get(project.get('production_language'), 'the project language')
    lines = [f'Use stable recurring voice identities. Spoken dialogue follows the project language: {language}.']
    global_style = _brief_series_style(voice.get('series_style'))
    if global_style:
        lines.append(global_style)
    audible = []
    for shot in shots:
        for line in shot.get('dialogue', []):
            sid = line.get('speaker_id')
            if sid in names and sid not in audible:
                audible.append(sid)
    cards = voice.get('cards', []) if isinstance(voice.get('cards'), list) else []
    for sid in audible:
        card = next((item for item in cards if isinstance(item, dict) and item.get('subject_id') == sid), None)
        if card:
            detail = ' '.join(_text(card.get(key)) for key in ('description', 'notes'))
            lines.append('Voice identity for ' + names[sid] + ': ' + (detail or 'keep the established named voice.')
                         + (' Uploaded clean voice audio takes priority for audible identity.' if card.get('has_audio') else ''))
    if audible:
        lines.append('Only ' + ', '.join(names[sid] for sid in audible) + ' speak' + ('s' if len(audible) == 1 else '')
                     + ' in this clip. Other visible characters do not speak or make speech-like lip movements.')
    for shot in shots:
        for line in shot.get('dialogue', []):
            sid = line.get('speaker_id')
            if sid not in names:
                continue
            tag = _text(line.get('language')) or language
            words = _text(line.get('text'))
            delivery = _text(line.get('delivery'))
            if delivery.lower().startswith('follow locked voice card'):
                delivery = ''
            prefix = names[sid] + ' ' + speaker_ids.get(sid, '')
            if line.get('voiceover') or sid in shot.get('offscreen_subject_ids', []):
                prefix += ' (off-screen voice)'
            lines.append(prefix + ': <d>[' + tag + '] ' + words + '</d>'
                         + (' Delivery: ' + _sentence(delivery) if delivery else ''))
            if line.get('voiceover') and sid in shot.get('visible_subject_ids', []):
                lines.append(names[sid] + "'s visible lips remain closed during the voiceover.")
    if not audible:
        lines.append('No scripted speech is assigned to this clip.')
    lines.append(audible_speech_lock(shots, names))
    return '\n\n'.join(lines)


def render_narrative_prompt(*, project, references, active, bound,
                            speaker_ids, shots, story_text,
                            style, custom, duration: Decimal, soundscape, music):
    """Render the five prose sections of the user-selected storyboard version."""
    subject_names = {item['id']: item['name'] for item in project.get('subjects', [])}
    style_text = ' '.join(_sentence(style.get(key)) for key in
                          ('genre', 'visual_style', 'vibe', 'lighting', 'color', 'notes') if _text(style.get(key)))
    visual = [style_text,
              f"Frame: {project.get('aspect_ratio', '16:9')}. Target clip duration: {duration:g} seconds."]
    if _text(story_text):
        visual.append(_sentence(story_text))
    for index, shot in enumerate(shots, 1):
        cast = [subject_names[sid] for sid in shot.get('visible_subject_ids', []) if sid in subject_names]
        parts = []
        setting = _text(shot.get('setting'))
        if setting and setting.casefold() not in story_text.casefold():
            parts.append(_sentence('Setting: ' + setting))
        if cast:
            parts.append('Visible cast: ' + ', '.join(cast) + '; one physical instance of each.')
            identity_lock = visible_identity_lock(
                project.get('subjects', []), shot.get('visible_subject_ids', []),
                lambda ident: subject_names.get(ident, 'character'))
            if identity_lock:
                parts.append(identity_lock)
            ensemble_direction = ensemble_stability_direction(
                shot.get('visible_subject_ids', []),
                lambda ident: subject_names.get(ident, 'character'))
            if ensemble_direction:
                parts.append(ensemble_direction)
        action = _text(shot.get('action'))
        if action:
            parts.append(_sentence(action))
        camera = _camera_note(shot.get('camera'), action)
        if camera:
            parts.append(camera)
        performance = _text(shot.get('performance'))
        if performance and performance.casefold() not in action.casefold():
            parts.append(_sentence(performance))
        objects = _object_notes(shot, subject_names)
        if objects:
            parts.append(objects)
        ending = _text(shot.get('final_state'))
        if ending and ending.casefold() not in action.casefold():
            parts.append('End state: ' + _sentence(ending))
        visual.append('[Beat ' + str(index) + '] ' + ' '.join(parts))
    if _text(custom):
        visual.append('Additional approved clip direction: ' + _text(custom))
    sounds = _distinct([*(shot.get('sound', '') for shot in shots), soundscape])
    sound = ' '.join(_sentence(item) for item in sounds) if sounds else 'No additional soundscape was specified.'
    on_screen = _distinct(subject_names[sid] for shot in shots
                          for sid in shot.get('visible_subject_ids', []) if sid in subject_names)
    stability = ('Keep one physical instance of each named visible character'
                 + (': ' + ', '.join(on_screen) if on_screen else '') +
                 '. Preserve the identity, proportions, outfit and distinctive details assigned to each reference. '
                 'Do not import extra cast or props from overview, environment or style images. '
                 'No duplicate bodies, identity swaps, prop reassignment, style drift, extra dialogue, '
                 'automatic subtitles, logos or watermarks. End in the stated final physical state.')
    return '\n\n'.join((
        'asset_roles:\n' + _asset_roles(references, active, bound, project),
        'visual_style_and_continuity:\n' + '\n\n'.join(part for part in visual if part),
        'dialogue_and_audio:\n' + _voice_and_dialogue(project, shots, subject_names, speaker_ids),
        'overall_soundscape:\n' + sound,
        'non_diegetic_music:\n' + (_text(music) or 'N/A'),
        'stability_constraints:\n' + stability,
    ))

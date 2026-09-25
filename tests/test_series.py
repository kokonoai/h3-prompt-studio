import pytest
import importlib
import shutil
import subprocess
from types import SimpleNamespace
from fastapi.testclient import TestClient

from backend.series import SeriesManager


def test_series_orders_multiple_projects_per_episode_without_moving_them(tmp_path):
    projects = {"11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
                "33333333-3333-4333-8333-333333333333"}

    def load(ident):
        if ident not in projects:
            raise ValueError("Production not found.")
        return {"id": ident}

    manager = SeriesManager(tmp_path, load)
    series = manager.create({"title": "Ten-part story", "episode_count": 2, "card_collection_id": None})
    assert len(series["episodes"]) == 2
    episodes = series["episodes"]
    episodes[0]["production_ids"] = ["11111111-1111-4111-8111-111111111111",
                                     "22222222-2222-4222-8222-222222222222"]
    episodes[1]["production_ids"] = ["33333333-3333-4333-8333-333333333333"]
    saved = manager.update(series["id"], {"episodes": episodes})
    assert saved["episodes"][0]["production_ids"] == episodes[0]["production_ids"]
    assert manager.list()[0]["part_count"] == 3
    assert projects == {"11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
                        "33333333-3333-4333-8333-333333333333"}

    duplicate = [dict(episode) for episode in episodes]
    duplicate[1]["production_ids"] = [episodes[0]["production_ids"][0]]
    with pytest.raises(ValueError, match="only once"):
        manager.update(series["id"], {"episodes": duplicate})
    assert manager.get(series["id"])["episodes"] == saved["episodes"]

    result = manager.delete(series["id"])
    assert result["projects_preserved"] and result["card_set_preserved"] and result["videos_preserved"]
    assert projects
    assert len(list((tmp_path / "series_archive").glob("*.json"))) == 1


def test_series_rejects_unavailable_project_and_bad_episode_count(tmp_path):
    manager = SeriesManager(tmp_path, lambda ident: (_ for _ in ()).throw(ValueError("Missing project")))
    with pytest.raises(ValueError, match="1–100"):
        manager.create({"title": "Story", "episode_count": 101})
    series = manager.create({"title": "Story", "episode_count": 1})
    episode = series["episodes"][0]
    episode["production_ids"] = ["11111111-1111-4111-8111-111111111111"]
    with pytest.raises(ValueError, match="Missing project"):
        manager.update(series["id"], {"episodes": [episode]})


def test_one_ready_episode_can_be_assembled_before_the_whole_script(tmp_path, monkeypatch):
    module = importlib.import_module("backend.app")
    ids = ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"]
    manager = SeriesManager(tmp_path, lambda ident: {"id": ident, "title": ident} if ident in ids else None)
    series = manager.create({"title": "Partly ready", "episode_count": 2})
    episodes = series["episodes"]
    for ep, ident in zip(episodes, ids):
        ep["production_ids"] = [ident]
    manager.update(series["id"], {"episodes": episodes})
    monkeypatch.setattr(module, "DATA", tmp_path)
    monkeypatch.setattr(module, "series_manager", lambda: manager)
    monkeypatch.setattr(module, "production_manager", lambda: SimpleNamespace(get=lambda ident: {"id": ident, "title": ident}))
    monkeypatch.setattr(module, "production_outputs", lambda ident: {
        "ready_count": 1 if ident == ids[0] else 0, "segment_count": 1,
        "all_ready": ident == ids[0], "final_ready": False,
        "signature": "ready-part" if ident == ids[0] else None,
        "final_url": None, "active_jobs": 0})
    overview = module.series_outputs(series["id"])
    assert not overview["all_ready"] and overview["signature"] is None
    assert overview["episodes"][0]["all_ready"] and overview["episodes"][0]["signature"]
    assert not overview["episodes"][1]["all_ready"]
    assert module.series_selection_outputs(series["id"], [1])["all_ready"]
    assert not module.series_selection_outputs(series["id"], [1, 2])["all_ready"]
    with pytest.raises(ValueError, match="distinct"):
        module.series_selection_outputs(series["id"], [1, 1])
    monkeypatch.setattr(module, "build_production_film", lambda _ident: tmp_path / "source.mp4")
    monkeypatch.setattr(module, "_series_dimensions", lambda _ident: (320, 180))
    def fake_normalize(_source, target, _width, _height):
        target.write_bytes(b"normalised-video")
        return target
    def fake_concat(_files, destination):
        destination.write_bytes(b"joined-episode")
    monkeypatch.setattr(module, "_normalize_series_file", fake_normalize)
    monkeypatch.setattr(module, "_concat_series_files", fake_concat)
    first = module.build_series_episode(series["id"], 1)
    assert first.is_file() and first.read_bytes() == b"joined-episode"
    with pytest.raises(ValueError, match="Every episode"):
        module.build_series_film(series["id"])


def test_episode_folder_opens_only_the_server_derived_result(tmp_path, monkeypatch):
    module = importlib.import_module("backend.app")
    series_id = "11111111-1111-4111-8111-111111111111"
    folder = tmp_path / "series_films" / series_id / "episodes" / "episode-01-proof"
    folder.mkdir(parents=True)
    opened = []
    monkeypatch.setattr(module, "DATA", tmp_path)
    monkeypatch.setattr(module.os, "startfile", lambda path: opened.append(path), raising=False)
    monkeypatch.setattr(module, "series_outputs", lambda _ident: {"episodes": [{"index": 1,
        "film_ready": True, "folder_path": str(folder)}]})
    with TestClient(module.app, base_url="http://127.0.0.1:8766", raise_server_exceptions=False) as client:
        endpoint = f"/api/series/{series_id}/film/episode/1/open"
        assert client.post(endpoint).status_code == 403
        response = client.post(endpoint, headers={"X-H3-Token": module.TOKEN,
            "Origin": "http://127.0.0.1:8766"}, json={"path": "C:\\Windows"})
        assert response.status_code == 200
        assert opened == [str(folder.resolve())]
        monkeypatch.setattr(module, "series_outputs", lambda _ident: {"episodes": [{"index": 1,
            "film_ready": True, "folder_path": str(tmp_path.parent)}]})
        assert client.post(endpoint, headers={"X-H3-Token": module.TOKEN,
            "Origin": "http://127.0.0.1:8766"}).status_code == 400
        assert opened == [str(folder.resolve())]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_assemble_two_episodes_from_ordered_project_parts(tmp_path, monkeypatch):
    module = importlib.import_module("backend.app")
    project_ids = ["11111111-1111-4111-8111-111111111111",
                   "22222222-2222-4222-8222-222222222222",
                   "33333333-3333-4333-8333-333333333333"]
    manager = SeriesManager(tmp_path, lambda ident: {"id": ident} if ident in project_ids else None)
    series = manager.create({"title": "Assembly test", "episode_count": 2})
    episodes = series["episodes"]
    episodes[0]["production_ids"] = project_ids[:2]
    episodes[1]["production_ids"] = project_ids[2:]
    manager.update(series["id"], {"episodes": episodes})
    sources = {}
    for index, ident in enumerate(project_ids):
        path = tmp_path / f"source-{index}.mp4"
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={['red', 'green', 'blue'][index]}:s=320x180:r=24:d=1"]
        if index != 2:
            command += ["-f", "lavfi", "-i", "anullsrc=r=32000:cl=stereo", "-t", "1", "-c:a", "aac"]
        result = subprocess.run(command + ["-c:v", "libx264", str(path)], capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        sources[ident] = path
    parts = lambda ids: [{"production_id": ident} for ident in ids]
    overview = {"all_ready": True, "signature": "test-signature",
        "episodes": [{"index": 1, "all_ready": True, "signature": "episode-one", "parts": parts(project_ids[:2])},
                     {"index": 2, "all_ready": True, "signature": "episode-two", "parts": parts(project_ids[2:])}]}
    monkeypatch.setattr(module, "DATA", tmp_path)
    monkeypatch.setattr(module, "series_outputs", lambda _ident: overview)
    monkeypatch.setattr(module, "build_production_film", lambda ident: sources[ident])
    monkeypatch.setattr(module, "production_outputs", lambda _ident: {
        "segments": [{"selected": {"width": 320, "height": 180}}]})
    full = module.build_series_film(series["id"])
    assert full.is_file() and full.stat().st_size > 0
    folder = full.parent
    assert (folder / "episode-01.mp4").is_file()
    assert (folder / "episode-02.mp4").is_file()
    assert (tmp_path / "series_films" / series["id"] / "episodes" / "episode-01-episode-one" / "episode-01.mp4").is_file()
    selected = module.build_series_selection(series["id"], [2, 1])
    assert selected.is_file() and selected.stat().st_size > 0
    selection = module.series_selection_outputs(series["id"], [2, 1])
    assert selection["episode_indices"] == [1, 2]
    assert selection["final_ready"] and selection["file_path"] == str(selected.resolve())
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(full)],
        capture_output=True, text=True, timeout=15)
    assert probe.returncode == 0
    assert 2.5 <= float(probe.stdout.strip()) <= 3.5
    audio = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index",
        "-of", "csv=p=0", str(full)], capture_output=True, text=True, timeout=15)
    assert audio.returncode == 0 and audio.stdout.strip()

"""The optional Krea image recipe must not replace native image workflows."""
from backend.asset_runs import (KREA_ENCODER, KREA_MODEL, KREA_VAE, MODELS,
                                _krea_catalog, _spec, build_graph)
from test_asset_runs import setup, spec, uid


def install_krea(server):
    server.info['EmptyLatentImage'] = {'input': {'required': {}}}
    for node, field, value in (
        ('UNETLoader', 'unet_name', KREA_MODEL),
        ('CLIPLoader', 'clip_name', KREA_ENCODER),
        ('CLIPLoader', 'type', 'krea2'),
        ('VAELoader', 'vae_name', KREA_VAE),
        ('KSampler', 'sampler_name', 'er_sde'),
    ):
        server.info[node]['input']['required'].setdefault(field, [[]])[0].append(value)


def test_krea_requires_complete_inventory_and_leaves_z_image_available(setup):
    manager, server, *_ = setup
    assert KREA_MODEL not in manager.options()['models']
    install_krea(server)
    options = manager.options()
    assert KREA_MODEL in options['models'] and MODELS[0] in options['models']
    assert options['default_model'] == MODELS[0]
    assert next(item for item in options['generators'] if item['id'] == KREA_MODEL)['kind'] == 'krea2'
    del server.info['SaveImage']
    assert KREA_MODEL not in _krea_catalog(server.info)[0]


def test_krea_uses_supplied_sampler_but_no_style_lora_or_preview_only_output():
    graph = build_graph(uid(), _spec(spec(model=KREA_MODEL, width=768, height=512)))
    assert graph['2']['inputs']['clip_name'] == KREA_ENCODER
    assert graph['2']['inputs']['type'] == 'krea2'
    assert graph['3']['inputs']['vae_name'] == KREA_VAE
    assert graph['7']['inputs']['steps'] == 8
    assert graph['7']['inputs']['sampler_name'] == 'er_sde'
    assert graph['9']['class_type'] == 'SaveImage'
    assert not any(node['class_type'] in ('LoraLoaderModelOnly', 'PreviewImage') for node in graph.values())

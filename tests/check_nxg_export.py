"""Check putting a figure into LEGO Batman 2 or LEGO Marvel Super Heroes headlessly (no window, no game run), on
a COPY of the game's GAME.DAT.

    python tests/check_nxg_export.py <work folder> <figure.glb> <base id, e.g. lb2:batman/batman>

Copies the installed game's GAME.DAT into <work folder>/<game>/, sends the figure through the server's
game-export call (replace, patch), then reads the archive back: the definition and the new model (and in LEGO
Marvel its images and shaders) are there, the definition names the model, the model reads with the figure's
pieces in its Body layer and the atlas as the Body material's texture, and every other file is untouched. Then
Restore original.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
STEAM = os.path.join('C:' + os.sep, 'Program Files (x86)', 'Steam', 'steamapps', 'common')
INSTALLS = {'lb2': os.path.join(STEAM, 'LEGO Batman 2'), 'lmsh': os.path.join(STEAM, 'LEGO Marvel Super Heroes'),
            'pirates': os.path.join(STEAM, 'LEGO\u00ae Pirates of the Caribbean The Video Game')}
BS = chr(92)
failures = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what, flush=True)
    if not cond:
        failures.append(what)


def main(work, glb_path, base):
    os.environ['FIGUREFORGE_HOME'] = tempfile.mkdtemp(prefix='ff_nx_')
    from forge import server, settings
    from forge.formats import modern
    from forge.formats.dat import DatArchive
    gid = base.split(':')[0]
    with open(os.path.join(ROOT, 'tests', 'games.json')) as f:
        games = json.load(f)
    inst = os.path.join(work, gid)
    os.makedirs(inst, exist_ok=True)
    dat = os.path.join(inst, 'GAME.DAT')
    if os.path.exists(dat + '.figureforge-backup'):
        os.remove(dat + '.figureforge-backup')
    shutil.copy2(os.path.join(INSTALLS[gid], 'GAME.DAT'), dat)
    before = {e.path: (e.offset, e.size) for e in DatArchive(dat).entries}
    settings.save({'games': {gid: games[gid]}, 'installs': {gid: inst}})
    httpd = server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    api = f'http://127.0.0.1:{httpd.server_address[1]}/api/'
    with open(glb_path, 'rb') as f:
        body = f.read()
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            api + f'game-export?base={base}&mode=replace&delivery=patch', data=body, method='POST'), timeout=900)
        out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        out = {'error': e.read().decode()}
    print(out)
    added = 3 if gid == 'lmsh' else 1                 # LEGO Marvel: the model, its images and its shaders
    # LEGO Batman 2 and LEGO Pirates read definitions from charsnimprojects.pac: it changes too
    pac = BS.join(['chars', 'animprojects.pac'])
    has_pac = gid in ('lb2', 'pirates')
    replaced = 2 if has_pac else 1
    check('error' not in out and out.get('replaced') == replaced and out.get('added') == added,
          f'{replaced} file(s) replaced, {added} added')
    arc = DatArchive(dat)
    by = {e.path.lower(): e for e in arc.entries}
    char_rel = base.split(':')[1].split('/')
    cd_path = BS.join(['chars', 'minifigs', char_rel[0], char_rel[1] + '.cd'])
    old = {p.lower() for p in before}
    new = [p for p in by if p not in old]
    model_path = next((p for p in new if p.endswith('_nxg.ghg')), '')
    stem = os.path.basename(model_path.replace(BS, '/'))[:-len('_nxg.ghg')]
    check(cd_path in by and bool(model_path), f'the definition and the model are in the archive ({model_path})')
    skip = {cd_path, pac} if has_pac else {cd_path}
    unchanged = sum(1 for p, v in before.items() if p.lower() not in skip and p.lower() in by
                    and (by[p.lower()].offset, by[p.lower()].size) == v)
    check(unchanged == len(before) - len(skip), f'every other file untouched ({unchanged} of {len(before) - len(skip)})')
    if has_pac:
        from forge.formats import pack
        held = {n.lower(): b for n, _k, b, _x in pack.read(arc.read(by[pac]))[0]}
        check(held.get(cd_path.split(BS, 1)[1]) == arc.read(by[cd_path]), 'animprojects.pac holds the new definition')
    # read the character back from the archive, as the game would find it
    look = tempfile.mkdtemp(prefix='ff_nx_look_')
    for rel in [cd_path] + new:
        dst = os.path.join(look, *rel.split(BS))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(arc.read(by[rel]))
    arc.f.close()
    ch = modern.Character(os.path.join(look, *cd_path.split(BS)), os.path.join(look, 'chars'))
    check(os.path.basename(ch.model_path).lower() == f'{stem}_nxg.ghg', 'the definition names the new model')
    from forge.formats import hser
    cd_bytes = open(os.path.join(look, *cd_path.split(BS)), 'rb').read()
    if hser.has_header(cd_bytes):
        # LEGO Marvel: the definition's resource header lists the model, as the games' build lists every model
        listed = set(hser.paths(hser.read(cd_bytes)[0]).values())
        want = model_path.replace(BS, '/').lower()
        check(want in listed, f'its resource header lists {want}')
    check(not ch.role_textures and not ch.attachments, 'no textures or attachments of its own')
    check(ch.default_layers == {2}, 'only the Body layer is shown')
    meshes = [m for p in ch.layers[2]['parts'] for m in p['meshes']]
    check(len(meshes) > 0 and all(ch.geometry(m) for m in meshes), f'the Body layer draws {len(meshes)} new parts')
    mats = {ch.material(m)['name'] for m in meshes}
    check(any(n.upper().startswith('BODY_FRONT') for n in mats), f'materials {sorted(mats)}')
    tex = {ch.material(m)['texture'] for m in meshes if ch.material(m)['name'].upper().startswith('BODY_FRONT')} - {-1}
    check(len(tex) == 1, 'the printed pieces wear one texture, the atlas')
    if tex:
        ref = ch.textures[tex.pop()]
        check(isinstance(ref, tuple) and ref[2] - ref[1] > 1000, f'the atlas image is found ({os.path.basename(ref[0])})')
    # a new character beside the others
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            api + f'game-export?base={base}&mode=new&name=ff_test&delivery=patch', data=body, method='POST'), timeout=900)
        out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        out = {'error': e.read().decode()}
    print(out)
    check('error' not in out, 'a new character is added')
    arc = DatArchive(dat)
    by = {e.path.lower(): e for e in arc.entries}
    new_cd = BS.join(['chars', 'minifigs', 'ff_test', 'ff_test.cd'])
    check(new_cd in by and new_cd.replace('.cd', '.txt') in by, 'its definition and text are in the archive')
    coll = arc.read(by[BS.join(['chars', 'collection.txt'])]).decode('latin1')
    check('collect "ff_test"' in coll, 'collection.txt names it')
    from forge.formats import pack
    names = [n.lower() for n, *_ in pack.read(arc.read(by[BS.join(['chars', 'charstxt.fpk'])]))[0]]
    check(new_cd.replace('.cd', '.txt') in names, 'charstxt.fpk holds its text')
    if has_pac:
        held = {n.lower() for n, *_ in pack.read(arc.read(by[pac]))[0]}
        check(new_cd.split(BS, 1)[1] in held, 'animprojects.pac holds its definition')
    look2 = tempfile.mkdtemp(prefix='ff_nx_look_')
    for rel in [p for p in by if p.startswith(BS.join(['chars', 'minifigs', 'ff_test'])) or
                p.startswith(os.path.dirname(model_path.replace(BS, '/')).replace('/', BS) + BS + 'ff')]:
        dst = os.path.join(look2, *rel.split(BS))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(arc.read(by[rel]))
    arc.f.close()
    try:
        ch2 = modern.Character(os.path.join(look2, *new_cd.split(BS)), os.path.join(look2, 'chars'))
        check(len([m for p in ch2.layers[2]['parts'] for m in p['meshes']]) > 0, 'the new character reads, wearing the figure')
    except Exception as e:
        check(False, f'the new character reads ({e})')
    shutil.rmtree(look2, ignore_errors=True)
    r = urllib.request.urlopen(urllib.request.Request(api + f'game-restore?game={gid}', data=b'', method='POST'))
    print(json.loads(r.read()))
    back = {e.path: (e.offset, e.size) for e in DatArchive(dat).entries}
    check(back == before, 'Restore original brings the archive back')
    httpd.shutdown()
    shutil.rmtree(look, ignore_errors=True)
    print('SUMMARY', 'all passed' if not failures else f'{len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:]))

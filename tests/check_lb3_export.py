"""Check putting a figure into LEGO Batman 3 headlessly (no window, no game run), on COPIES of the game's archives.

    python tests/check_lb3_export.py <work folder> <figure.glb> [base id, default lb3:adamwest/adamwest]

Copies the installed game's GAME*.DAT into <work folder>/lb3/, sends the figure through the server's game-export
call (replace, patch), then reads the archives back: the definition, its .res, the new model, its images,
shaders and .res are there; the definition names the model and its resource header lists it; each .res is the
one the build would write from the headers (hser.pc_resources); the model reads with the figure's pieces in its
Body layer and the atlas as the Body material's texture; every other file is untouched. Then a new character,
then Restore original.
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
INSTALL = os.path.join('C:' + os.sep, 'Program Files (x86)', 'Steam', 'steamapps', 'common',
                       'LEGO Batman 3 Beyond Gotham')
BS = chr(92)
failures = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what, flush=True)
    if not cond:
        failures.append(what)


def everything(inst):
    from forge.formats.dat import DatArchive
    out = {}
    for n in sorted(os.listdir(inst)):
        if n.upper().endswith('.DAT'):
            for e in DatArchive(os.path.join(inst, n)).entries:
                out[e.path.lower()] = (n, e.offset, e.size)
    return out


def read_files(inst, rels):
    from forge.formats.dat import DatArchive
    want, got = {r.lower() for r in rels}, {}
    for n in sorted(os.listdir(inst)):
        if n.upper().endswith('.DAT'):
            arc = DatArchive(os.path.join(inst, n))
            for e in arc.entries:
                if e.path.lower() in want:
                    got[e.path.lower()] = arc.read(e)
            arc.f.close()
    return got


def call(api, query, body):
    try:
        r = urllib.request.urlopen(urllib.request.Request(api + query, data=body, method='POST'), timeout=1800)
        return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {'error': e.read().decode()}


def unpack(look, files):
    for rel, data in files.items():
        dst = os.path.join(look, *rel.split(BS))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(data)


def check_res(files, game_root, rel):
    """rel's .res equals what pc_resources works out from the files (the new ones first, then the game's)."""
    from forge.formats import hser

    def own(path):
        data = files.get(path.replace('/', BS))
        if data is None:
            p = os.path.join(game_root, *path.split('/'))
            data = open(p, 'rb').read() if os.path.exists(p) else None
        return hser.entries(hser.read(data)[0]) if data is not None and hser.has_header(data) else None

    res = files.get(rel + '.res')
    if res is None:
        return False
    want = hser.pc_resources(own(rel.replace(BS, '/')) or [], own)
    return hser.entries(hser.read(res)[0]) == want and len(want) > 0


def main(work, glb_path, base='lb3:adamwest/adamwest'):
    os.environ['FIGUREFORGE_HOME'] = tempfile.mkdtemp(prefix='ff_lb3_')
    from forge import server, settings
    from forge.formats import hser, modern
    with open(os.path.join(ROOT, 'tests', 'games.json')) as f:
        games = json.load(f)
    inst = os.path.join(work, 'lb3')
    os.makedirs(inst, exist_ok=True)
    for n in os.listdir(INSTALL):
        if n.upper().endswith('.DAT'):
            for old in (os.path.join(inst, n + '.figureforge-backup'),):
                if os.path.exists(old):
                    os.remove(old)
            src, dst = os.path.join(INSTALL, n), os.path.join(inst, n)
            if not (os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src)):
                shutil.copy2(src, dst)
    before = everything(inst)
    settings.save({'games': {'lb3': games['lb3']}, 'installs': {'lb3': inst}})
    httpd = server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    api = f'http://127.0.0.1:{httpd.server_address[1]}/api/'
    with open(glb_path, 'rb') as f:
        body = f.read()

    out = call(api, f'game-export?base={base}&mode=replace&delivery=patch', body)
    print(out)
    check('error' not in out and out.get('replaced') == 4 and out.get('added') == 4,
          'four files replaced (the definition and its .res, the portrait and the sheet of portraits), four added '
          '(model, images, shaders, model .res)')
    after = everything(inst)
    new = sorted(p for p in after if p not in before)
    changed = sorted(p for p in before if after.get(p) != before[p])
    print('new', new, 'changed', changed)
    folder, name = base.split(':')[1].split('/')
    cd_rel = BS.join(['chars', 'minifig', folder, name + '.cd'])
    model_rel = next((p for p in new if p.endswith('_dx11.ghg')), '')
    sheet = BS.join(['icons', 'characters', 'charactericontpage_dx11.tsh'])
    icon = next((p for p in changed if p.startswith(BS.join(['icons', 'characters', ''])) and p.endswith('_dx11.tex')), '')
    check(sorted(changed) == sorted([cd_rel, cd_rel + '.res', sheet, icon]) and icon,
          f'only the definition and its .res, the portrait ({icon}) and the sheet changed')
    stem = os.path.basename(model_rel.replace(BS, '/'))[:-len('_dx11.ghg')]
    check(bool(model_rel) and {model_rel + '.res', model_rel[:-4] + '.nxg_textures',
                               model_rel[:-4] + '.pc_shaders'} <= set(new), f'the model and its companions ({model_rel})')
    files = read_files(inst, [cd_rel, cd_rel + '.res'] + new)
    cd = files.get(cd_rel, b'')
    listed = set(hser.paths(hser.read(cd)[0]).values()) if hser.has_header(cd) else set()
    check(model_rel.replace(BS, '/') in listed, 'the definition\'s resource header lists the model')
    check(check_res(files, games['lb3'], cd_rel), 'the definition\'s .res is the one the build would write')
    check(check_res(files, games['lb3'], model_rel), 'the model\'s .res is the one the build would write')
    res = hser.entries(hser.read(files.get(cd_rel + '.res', b''))[0]) if files.get(cd_rel + '.res') else []
    check(any(e[1] == model_rel.replace(BS, '/') for e in res), 'the definition\'s .res loads the model')

    look = tempfile.mkdtemp(prefix='ff_lb3_look_')
    unpack(look, files)
    ch = modern.Character(os.path.join(look, *cd_rel.split(BS)), os.path.join(look, 'chars'))
    check(os.path.basename(ch.model_path).lower() == f'{stem}_dx11.ghg', 'the definition names the new model')
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
        check(isinstance(ref, tuple) and ref[2] - ref[1] > 1000, f'the atlas image is found ({ref[:1]})')

    out = call(api, f'game-export?base={base}&mode=new&name=ff_test&delivery=patch', body)
    print(out)
    check('error' not in out, 'a new character is added')
    after2 = everything(inst)
    new_cd = BS.join(['chars', 'minifig', 'ff_test', 'ff_test.cd'])
    check({new_cd, new_cd + '.res', new_cd[:-3] + '.txt'} <= set(after2), 'its definition, .res and text are there')
    files2 = read_files(inst, [new_cd, new_cd + '.res', BS.join(['chars', 'collection.txt'])] +
                        [p for p in after2 if p.endswith('_dx11.ghg') and p not in before] +
                        [p for p in after2 if p not in before and BS + 'ff' in p])
    coll = files2.get(BS.join(['chars', 'collection.txt']), b'').decode('latin1')
    check('collect "ff_test"' in coll, 'collection.txt names it')
    check(check_res(files2, games['lb3'], new_cd), 'its .res is the one the build would write')
    from forge import gameportrait
    check(BS.join(['icons', 'characters', 'ff_test_dx11.tex']) in after2, 'it has a portrait of its own')
    sheet_now = read_files(inst, [sheet])[sheet]
    check(gameportrait.fnv1_upper('ff_test') in gameportrait.tsh_squares(sheet_now)[0], 'the sheet has a square for it')
    proj = read_files(inst, [BS.join(['chars', 'black_minifig.cpj'])])[BS.join(['chars', 'black_minifig.cpj'])]
    check(b'ff_test' in proj, 'the character project lists it')
    look2 = tempfile.mkdtemp(prefix='ff_lb3_look_')
    unpack(look2, files2)
    try:
        ch2 = modern.Character(os.path.join(look2, *new_cd.split(BS)), os.path.join(look2, 'chars'))
        check(len([m for p in ch2.layers[2]['parts'] for m in p['meshes']]) > 0,
              'the new character reads, wearing the figure')
    except Exception as e:
        check(False, f'the new character reads ({e})')

    r = urllib.request.urlopen(urllib.request.Request(api + 'game-restore?game=lb3', data=b'', method='POST'))
    print(json.loads(r.read()))
    check(everything(inst) == before, 'Restore original brings the archives back')
    httpd.shutdown()
    shutil.rmtree(look, ignore_errors=True)
    shutil.rmtree(look2, ignore_errors=True)
    print('SUMMARY', 'all passed' if not failures else f'{len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:]))

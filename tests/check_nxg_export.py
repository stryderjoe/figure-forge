"""Check putting a figure into LEGO Batman 2 headlessly (no window, no game run), on a COPY of its GAME.DAT.

    python tests/check_nxg_export.py <work folder> <figure.glb> [<base id>]

Copies the installed game's GAME.DAT into <work folder>/lb2/, sends the figure through the server's
game-export call (replace, patch), then reads the archive back: the definition and the new model are there,
the definition names the model, the model reads with the figure's pieces in its Body layer and the atlas as
the Body material's texture, and every other file is untouched. Then Restore original.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
INSTALL = r'C:\Program Files (x86)\Steam\steamapps\common\LEGO Batman 2'
failures = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what, flush=True)
    if not cond:
        failures.append(what)


def main(work, glb_path, base='lb2:batman/batman'):
    os.environ['FIGUREFORGE_HOME'] = tempfile.mkdtemp(prefix='ff_nx_')
    from forge import server, settings
    from forge.formats import modern
    from forge.formats.dat import DatArchive
    with open(os.path.join(ROOT, 'tests', 'games.json')) as f:
        games = json.load(f)
    inst = os.path.join(work, 'lb2')
    os.makedirs(inst, exist_ok=True)
    dat = os.path.join(inst, 'GAME.DAT')
    for leftover in (dat + '.figureforge-backup',):
        if os.path.exists(leftover):
            os.remove(leftover)
    shutil.copy2(os.path.join(INSTALL, 'GAME.DAT'), dat)
    before = {e.path: (e.offset, e.size) for e in DatArchive(dat).entries}
    settings.save({'games': {'lb2': games['lb2']}, 'installs': {'lb2': inst}})
    httpd = server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{httpd.server_address[1]}/api/game-export?base={base}&mode=replace&delivery=patch'
    with open(glb_path, 'rb') as f:
        body = f.read()
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, data=body, method='POST'), timeout=900)
        out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        out = {'error': e.read().decode()}
    print(out)
    check('error' not in out and out.get('replaced') == 1 and out.get('added') == 1, 'one file replaced, one added')
    arc = DatArchive(dat)
    by = {e.path.lower(): e for e in arc.entries}
    char_rel = base.split(':')[1].split('/')
    cd_path = f'chars\\minifigs\\{char_rel[0]}\\{char_rel[1]}.cd'
    stem = char_rel[1] + '_ff'
    model_path = f'chars\\minifigs\\figureforge\\{stem}_nxg.ghg'
    check(cd_path in by and model_path in by, 'the definition and the model are in the archive')
    unchanged = sum(1 for p, v in before.items() if p.lower() != cd_path and p.lower() in by
                    and (by[p.lower()].offset, by[p.lower()].size) == v)
    check(unchanged == len(before) - 1, f'every other file untouched ({unchanged} of {len(before) - 1})')
    # read the character back from the archive, as the game would find it
    look = tempfile.mkdtemp(prefix='ff_nx_look_')
    for rel in (cd_path, model_path):
        dst = os.path.join(look, *rel.split('\\'))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(arc.read(by[rel]))
    arc.f.close()
    ch = modern.Character(os.path.join(look, *cd_path.split('\\')), os.path.join(look, 'chars'))
    check(os.path.basename(ch.model_path).lower() == f'{stem}_nxg.ghg', 'the definition names the new model')
    check(not ch.role_textures and not ch.attachments, 'no textures or attachments of its own')
    check(ch.default_layers == {2}, 'only the Body layer is shown')
    body = ch.layers[2]
    meshes = [m for p in body['parts'] for m in p['meshes']]
    check(len(meshes) > 0 and all(ch.geometry(m) for m in meshes), f'the Body layer draws {len(meshes)} new parts')
    mats = {ch.material(m)['name'] for m in meshes}
    check(any(n.startswith('BODY_FRONT') for n in mats), f'materials {sorted(mats)}')
    tex = {ch.material(m)['texture'] for m in meshes} - {-1}
    check(len(tex) == 1, 'one texture, the atlas')
    # restore
    r = urllib.request.urlopen(urllib.request.Request(
        f'http://127.0.0.1:{httpd.server_address[1]}/api/game-restore?game=lb2', data=b'', method='POST'))
    print(json.loads(r.read()))
    back = {e.path: (e.offset, e.size) for e in DatArchive(dat).entries}
    check(back == before, 'Restore original brings the archive back')
    httpd.shutdown()
    print('SUMMARY', 'all passed' if not failures else f'{len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:]))

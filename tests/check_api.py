"""Check the API against your own extracted games (no window).

    python tests/check_api.py <lb1 root> [<indy root> <tcs root>]

Starts the server on a free port with a throwaway settings folder, then: indexes each game, loads a base,
every default part of it, a texture and a clip, and fits parts across the two skeleton schemes.
"""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['FIGUREFORGE_HOME'] = tempfile.mkdtemp(prefix='ff_test_')
from forge import server  # noqa: E402

failures = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what, flush=True)
    if not cond:
        failures.append(what)


def main(argv):
    roots = dict(zip(('lb1', 'indy', 'tcs'), argv))
    httpd = server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base_url = f'http://127.0.0.1:{httpd.server_address[1]}'

    def get(path, raw=False):
        with urllib.request.urlopen(base_url + path, timeout=600) as r:
            data = r.read()
        return data if raw else json.loads(data)

    def post(path, obj):
        req = urllib.request.Request(base_url + path, json.dumps(obj).encode(), {'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

    got = post('/api/settings', {'games': roots})
    check(sorted(got['ready']) == sorted(roots), f'settings: games ready {got["ready"]}')
    check(b'<html' in get('/', raw=True).lower(), 'user interface page served')
    picks = {}
    for gid in roots:
        t = time.time()
        idx = get(f'/api/characters?game={gid}')
        chars = idx['characters']
        humans = [c for c in chars if c['humanoid']]
        errors = [c for c in chars if c.get('error')]
        check(not errors, f'{gid}: {len(chars)} characters indexed, {len(humans)} humanoid, '
                          f'{sum(len(c["parts"]) for c in chars)} parts, {len(errors)} errors ({time.time() - t:.0f} s)')
        picks[gid] = humans
    # one base per game: its skeleton, every default part, one texture, one clip
    for gid, humans in picks.items():
        for want in ({'lb1': 'batman', 'indy': 'indianajones', 'tcs': 'lukeskywalker_tatooine'}[gid],):
            c = next((h for h in humans if h['name'].lower() == want), humans[0])
            sk = get(f'/api/base?char={c["id"]}')
            check(len(sk['bones']) > 20, f'{gid} {c["name"]}: skeleton {len(sk["bones"])} bones, scheme {sk["scheme"]}')
            n_mesh = 0
            tex = None
            for p in c['parts']:
                if not p['default']:
                    continue
                part = get(f'/api/part?id={urllib.request.quote(p["id"])}&base={c["id"]}')
                n_mesh += len(part['meshes'])
                for m in part['meshes']:
                    tex = tex or m['material']['texture']
            check(n_mesh > 0, f'{gid} {c["name"]}: default parts give {n_mesh} meshes')
            if tex:
                png = get(tex, raw=True)
                check(png[:4] == b'\x89PNG', f'{gid}: texture served as PNG ({len(png)} bytes)')
            clips = get(f'/api/clips?char={c["id"]}')['clips']
            check(len(clips) > 0, f'{gid} {c["name"]}: {len(clips)} clips declared')
            if clips:
                pick = next((x for x in clips if (x['action'] or '').lower() in ('run', 'walk')), clips[0])
                clip = get(f'/api/clip?char={c["id"]}&name={urllib.request.quote(pick["name"])}')
                check(clip['frames'] > 0 and clip['tracks'], f'{gid}: clip {pick["name"]} {clip["frames"]} frames, '
                                                            f'{len(clip["tracks"])} tracks')
    # across schemes and games: a Star Wars head on Batman, Batman's cowl on Luke
    if 'tcs' in picks and 'lb1' in picks:
        bat = next(h for h in picks['lb1'] if h['name'].lower() == 'batman')
        luke = next((h for h in picks['tcs'] if h['name'].lower() == 'lukeskywalker_tatooine'), picks['tcs'][0])
        for donor, base, slot in ((luke, bat, 'head'), (bat, luke, 'headgear'), (luke, bat, 'torso')):
            p = next((p for p in donor['parts'] if p['slot'] == slot and p['default']), None)
            if p is None:
                check(False, f'{donor["name"]} has no default {slot}')
                continue
            part = get(f'/api/part?id={urllib.request.quote(p["id"])}&base={base["id"]}')
            check(part['meshes'] and all(len(m['position']) for m in part['meshes']),
                  f'{donor["name"]} {slot} fitted onto {base["name"]}')
    httpd.shutdown()
    print(f'SUMMARY {"all passed" if not failures else str(len(failures)) + " failed"}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

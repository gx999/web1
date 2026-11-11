"""생활관 미리보기 빌드: office.src.html 에 assets/ 의 PNG를 data URI로 넣어 dist/ 한 파일로 만든다.
assets/ 는 Pixel Agents(MIT) 커밋 b1bafd0의 webview-ui/public/assets 원본 그대로(라이선스는 assets/LICENSES.md)."""
import base64, glob, json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
src = os.path.join(here, 'assets')
keys = {}
for f in sorted(glob.glob(os.path.join(src, '**', '*.png'), recursive=True)):
    rel = os.path.relpath(f, src).replace(os.sep, '/')
    name = os.path.splitext(os.path.basename(f))[0]
    if rel.startswith('pets/'):
        name = 'pet_' + rel.split('/')[1]
    if rel.startswith('characters/') and name == 'char_4':
        continue
    keys[name] = 'data:image/png;base64,' + base64.b64encode(open(f, 'rb').read()).decode()
html = open(os.path.join(here, 'office.src.html'), encoding='utf-8').read().replace('/*__ASSETS__*/{}', json.dumps(keys))
sample = os.path.join(here, 'sample-state.json')
if os.path.exists(sample):
    html = html.replace('/*__SAMPLE__*/null', json.dumps(json.load(open(sample, encoding='utf-8')), ensure_ascii=False))
os.makedirs(os.path.join(here, 'dist'), exist_ok=True)
out = os.path.join(here, 'dist', 'lab-office-life.html')
open(out, 'w', encoding='utf-8').write(html)
print(out, len(html), 'bytes,', len(keys), 'images')

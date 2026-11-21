import sys, struct, os
sys.path.insert(0, os.path.join(os.getcwd(), "packages", "reco-ui", "src"))
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter, QIcon, QGuiApplication
from PyQt6.QtSvg import QSvgRenderer
app = QGuiApplication(sys.argv)
svg_path = "packages/reco-ui/src/reco_ui/assets/reco.svg"
out_dir = "packages/reco-ui/src/reco_ui/assets"
renderer = QSvgRenderer(svg_path)
assert renderer.isValid(), "SVG invalid"
sizes = [256, 128, 64, 48, 32, 24, 16]
pngs = {}
for s in sizes:
    img = QImage(s, s, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p)
    p.end()
    png_path = os.path.join(out_dir, "reco-%d.png" % s)
    img.save(png_path, "PNG")
    pngs[s] = open(png_path, "rb").read()
header = struct.pack("<HHH", 0, 1, len(sizes))
offset = 6 + 16 * len(sizes)
entries = b""
blobs = b""
for s in sizes:
    w = s if s < 256 else 0
    data = pngs[s]
    entries += struct.pack("<BBBBHHII", w, h if False else 0, 0, 0, 1, 32, len(data), offset) if False else struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(data), offset)
    blobs += data
    offset += len(data)
ico_path = os.path.join(out_dir, "reco.ico")
with open(ico_path, "wb") as f:
    f.write(header + entries + blobs)
print("ICO assembled:", os.path.getsize(ico_path), "bytes,", len(sizes), "sizes")
icon = QIcon(ico_path)
print("QIcon availableSizes:", [sz.width() for sz in icon.availableSizes()])

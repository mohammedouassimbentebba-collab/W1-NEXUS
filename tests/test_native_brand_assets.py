from __future__ import annotations
import hashlib, json, struct, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; PROD=ROOT/"brand"/"production"
def png_dims(p):
 b=p.read_bytes(); assert b.startswith(b"\x89PNG\r\n\x1a\n"); w,h,bd,ct=struct.unpack(">IIBB",b[16:26]); return w,h,ct
class NativeBrandAssetTests(unittest.TestCase):
 def test_transparent_png(self):
  self.assertEqual(png_dims(PROD/"w1-nexus-symbol-1024.png"),(1024,1024,6))
 def test_svg_is_vector_path(self):
  s=(PROD/"w1-nexus-symbol.svg").read_text(); self.assertIn('<path fill="#00D9FF"',s); self.assertNotIn("<image",s); self.assertNotIn("base64",s)
 def test_ico_is_multi_resolution(self):
  b=(PROD/"w1-nexus.ico").read_bytes(); r,k,c=struct.unpack("<HHH",b[:6]); self.assertEqual((r,k),(0,1)); self.assertGreaterEqual(c,7)
 def test_splash_dimensions(self): self.assertEqual(png_dims(PROD/"w1-nexus-splash.png")[:2],(1920,1080))
 def test_hashes_match(self):
  m=json.loads((ROOT/"brand/brand-manifest.json").read_text());
  for d in m["production_assets"].values():
   p=ROOT/d["path"]; self.assertTrue(p.is_file()); self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),d["sha256"]); self.assertEqual(d["status"],"ready")
 def test_master_pin(self):
  a=json.loads((PROD/"ASSET-MANIFEST.json").read_text()); m=json.loads((ROOT/"brand/brand-manifest.json").read_text()); self.assertEqual(a["source_master_sha256"],m["master_concept"]["sha256"]); self.assertFalse(m["production_derivation"]["generative_redesign"])
if __name__=="__main__": unittest.main()

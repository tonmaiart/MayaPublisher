**Bake facial texture ไป Unity** — image sequence ของ material ที่มีแอนิเมชันของทุกตัวละคร (ตา, ปาก, ...)

1. Import reference ทั้งหมดเข้า scene และลบ namespace ทิ้ง (เหมือน UnityShot)
2. หา material ทุกตัวบน mesh ตัวละคร (`*_Geo`) ที่สีมีแอนิเมชัน — มีคีย์/driven key, ขับด้วย expression, file texture ที่เปิด Use Image Sequence หรือ**หน้า LED** (`ledFaceMaterial` ของ LedFaceCapture เช่น FaceShield ของ Jacob)
3. ทีละ material: เอาสีแบบ flat (ไม่รับแสง, ปิด color management, เปิด Textured) ไปแปะบน plane ชั่วคราว แล้ว playblast ตลอดช่วง timeline ที่ 512×512
   - หน้า LED: bake จาก node `ledFaceMaterial` ที่ขับสีอยู่, สลับทุก panel เป็น renderer **LED Face Capture**, ใช้ Lighting แบบ flat และพื้นหลังดำ (bake เสร็จคืนค่าเดิม)
   - ถ้า plug-in `led_face_capture` ไม่ได้โหลดตอนเปิด scene จะ fail พร้อมบอกให้เปิด MayaToolkit > LedFaceCapture ใน repo
4. เขียน `<material>.<frame>.png` ลงโฟลเดอร์ version ตรงๆ (หน้า LED ใช้ชื่อ material เช่น `MTL_Capture.1001.png`)
5. ฝั่ง Unity: Shot Importer อ่านจากช่อง **Published Textures** import มาพร้อมช็อต แล้วสร้าง Texture2DArray `<ช็อต>_<material>` ต่อ material

> ต้องรันใน Maya แบบมีหน้าต่าง (มัน playblast) — batch mode เป็น `gui`. Publish คู่กับ UnityShot ซึ่ง export skeleton/กล้องแบบ headless
> ชื่อช็อตมาจากชื่อโฟลเดอร์ที่เก็บไฟล์ scene อยู่ เหมือน UnityShot

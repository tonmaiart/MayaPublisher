**Publish ช็อตแอนิเมชันไป Unity** — skeleton (ไม่มี mesh) แยกไฟล์ตามตัวละคร + กล้อง

1. Import reference ทั้งหมดเข้า scene และลบ namespace ทิ้ง
2. หา mesh ตัวละครทั้งหมด (`*_Geo`) แยกตามชื่อตัวละคร แล้วแก้ชื่อ material และตรวจ face set ของ material
3. หา skeleton ของแต่ละตัวละครจากกลุ่ม `DeformationSystem` แล้ว bake animation ลง joint ตลอดช่วง timeline
4. ตัด connection ของ visibility, ลบคีย์ visibility และเปิด visibility ทุก joint, ปิด `segmentScaleCompensate` และลบ node ที่ไม่ใช่ joint ใต้ root (ยกเว้นกล้อง)
5. Export FBX ทีละตัวละคร — ย้าย root joint ไป world, เก็บค่า scale ที่ถูกชดเชยไว้, export เป็น `<ช็อต>_<ตัวละคร>_anim_<version>.fbx` แล้วย้าย root กลับที่เดิม
6. เขียน `<ช็อต>_metadata.json` เก็บค่า `scale_min` ของแต่ละตัวละคร
7. หากล้อง `camshot` → bake transform + shape, ลบ constraint และ child, ย้ายไป world, เปลี่ยนชื่อเป็น `RenderCam` แล้ว export `<ช็อต>_camera.fbx`

> ชื่อช็อตมาจากชื่อโฟลเดอร์ที่เก็บไฟล์ scene อยู่

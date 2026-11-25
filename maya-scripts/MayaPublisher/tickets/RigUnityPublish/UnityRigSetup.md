**Publish rig ไป Unity** — skeleton + mesh ต่อตัวละคร เป็น FBX

1. Import reference ทั้งหมด, ลบ namespace และลบกลุ่ม Delete_Grp
2. หา mesh ตัวละครทั้งหมด (`*_Geo`) แยกตามชื่อตัวละคร แล้วแก้ชื่อ material และตรวจ face set ของ material
3. เลือก joint ทั้งหมดใน `DeformSet` แล้ว bake animation ตลอดช่วง timeline
4. ตัด connection ทั้งหมดที่ขับ joint (translate/rotate/scale/visibility), ลบคีย์ visibility และเปิด visibility ทุก joint
5. ลบ node ที่ไม่ใช่ joint ใต้ root joint แล้วย้าย root joint ไป world
6. ทุก mesh `*_Geo`: ลบคีย์, ตัด connection, เปิด visibility แล้วย้ายไป world
7. ปิด Smooth Mesh Preview ของ mesh ทุกตัว
8. Export FBX ทีละตัวละคร (joint ใน `DeformSet` + mesh ของตัวนั้น) เป็น `<ตัวละคร>.fbx`

> ทำงานบน scene ที่เปิดอยู่ — หลัง publish อย่าเซฟทับไฟล์ทำงาน

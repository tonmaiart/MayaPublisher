**Publish rig ให้ทีม Animation** — clean แล้ว export เป็น Maya Ascii

1. รัน `Scene.clean_up_scene()` ของ tmlib — ถ้าไม่ผ่านจะหยุด publish
2. ใช้ชื่อตัวละครจากชื่อไฟล์ scene (block แรกก่อน `_` เช่น `KBA030_rig_v012.ma` → `KBA030`)
3. ลบกลุ่ม Delete_Grp และ node ที่ห้ามติดไป: `Delete_Grp`, `FitSkeleton`, `FaceFitSkeleton`, `_dwpicker_data`
4. ลบ node ของ ngSkinTools (v1/v2) และรายการ "requires ngSkinTools" — น้ำหนัก skin ยังอยู่ครบ
5. Remove Unused Influences ทุก skinCluster
6. AdvancedSkeleton > Game Engine > Scale Behaviour: **Enable** (ต้องมี `ADVANCEDSKELETON_ROOT`)
7. เปลี่ยนชื่อกลุ่มบนสุด `Group` เป็นชื่อตัวละคร
8. Delete Unused Nodes (Hypershade)
9. ตรวจซ้ำว่าไม่มี node ที่ต้องลบหลงเหลือ — ถ้ามีจะหยุด publish
10. Export scene ที่ clean แล้วเป็น `<ตัวละคร>_v<version>.ma`

> ทำงานบน scene ที่เปิดอยู่ — หลัง publish อย่าเซฟทับไฟล์ทำงาน

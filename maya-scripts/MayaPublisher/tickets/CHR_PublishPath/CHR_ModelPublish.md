**Publish โมเดลตัวละคร** — clean แล้ว export เป็น Maya Ascii

1. ลบกลุ่ม `Delete_Grp` ถ้ามีใน scene
2. Delete Unused Nodes (Hypershade) — ลบ node และ material ที่ไม่ได้ใช้งาน
3. ตั้ง Preview Division Levels (`smoothLevel`) ของ mesh shape ทุกตัวใน scene เป็น 0 — ตัวที่ล็อกหรือมี connection อยู่จะข้ามพร้อม warning
4. Export scene ที่ clean แล้วเป็น `CHR_ModelPublish_v<version>.ma` ลงโฟลเดอร์ version ใหม่ (`vXXX`)

> ทำงานบน scene ที่เปิดอยู่ — หลัง publish อย่าเซฟทับไฟล์ทำงาน

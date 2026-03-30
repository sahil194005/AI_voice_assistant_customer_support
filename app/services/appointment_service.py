from sqlalchemy import text

from app.db.session import SessionLocal


ALLOWED_DEPARTMENTS = {"cardiology", "dermatology", "general"}


def create_appointment(phone: str, department: str, date: str) -> str:
    try:
        db = SessionLocal()
        query = text("SELECT * FROM customer_support_db.patients WHERE phone = :phone_val")
        patient = db.execute(query, {"phone_val": phone}).fetchone()

        if not patient:
            db.execute(
                text("INSERT INTO customer_support_db.patients (phone, name) VALUES (:phone_val, 'Guest')"),
                {"phone_val": phone},
            )
            db.commit()
            patient = db.execute(query, {"phone_val": phone}).fetchone()

        patient_id = patient[0]
        db.execute(
            text(
                """
                INSERT INTO customer_support_db.appointments (patient_id, department, date, time_slot)
                VALUES (:patient_id, :department, :date, '10:00 AM')
                """
            ),
            {"patient_id": patient_id, "department": department, "date": date},
        )
        db.commit()
        db.close()
        return "Your appointment is booked for 10 AM"
    except Exception as error:
        return f"An error occurred: {error}"

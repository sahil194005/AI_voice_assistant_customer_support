from db import SessionLocal
from sqlalchemy import text


def create_appointment(phone, department, date):
    try:
        db = SessionLocal()
        query = text("SELECT * FROM customer_support_db.patients WHERE phone = :phone_val")
    
        # Pass the variable in a dictionary to execute()
        result = db.execute(query, {"phone_val": phone})
        patient = result.fetchone()

        if not patient:
            db.execute(text("INSERT INTO customer_support_db.patients (phone, name) VALUES (:phone_val, 'Guest')"), {"phone_val": phone})
            db.commit()

            patient = db.execute(query, {"phone_val": phone}).fetchone()

        patient_id = patient[0]

        # Create appointment
        db.execute(
            text("""
            INSERT INTO customer_support_db.appointments (patient_id, department, date, time_slot)
            VALUES (:patient_id, :department, :date, '10:00 AM')
            """),
            {"patient_id": patient_id, "department": department, "date": date}
        )
        db.commit()

        db.close()

        return "Your appointment is booked for 10 AM"
    except Exception as e:
        return f"An error occurred: {e}"

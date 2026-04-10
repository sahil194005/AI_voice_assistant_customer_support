from datetime import datetime

from sqlalchemy import text

from app.db.session import SessionLocal



def get_available_departments() -> list[str]:
    try:
        return ["Cardiology", "Dermatology", "Neurology", "Pediatrics", "Orthopedics"]
    except Exception as error:
        print(f"get_available_departments failed: {error}")
        raise


def create_appointment(
    phone: str,
    department: str,
    date: str,
    time_slot: str,
) -> str:
    try:
        db = None
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
                INSERT INTO customer_support_db.appointments (
                    patient_id,
                    department,
                    date,
                    time_slot
                )
                VALUES (
                    :patient_id,
                    :department,
                    :date,
                    :time_slot
                )
                """
            ),
            {
                "patient_id": patient_id,
                "department": department,
                "date": date,
                "time_slot": time_slot,
            },
        )
        db.commit()
        human_time = datetime.strptime(time_slot, "%H:%M:%S").strftime("%I:%M %p").lstrip("0")
        return f"Your appointment is booked for {date} at {human_time}"
    except Exception as error:
        print(f"create_appointment failed: {error}")
        raise
    finally:
        if db is not None:
            db.close()

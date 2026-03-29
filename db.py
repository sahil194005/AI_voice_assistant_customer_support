from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "mysql+pymysql://root:root@localhost:3306/customer_support_db"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

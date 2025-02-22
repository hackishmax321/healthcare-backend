# main.py
from fastapi import FastAPI, HTTPException, File, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, Field
import bcrypt
import os
import shutil
from typing import List, Optional, Tuple, Dict, Any
from firestore_db import get_firestore_client
from excercise_monitor import exe_launch
from face_detection import FaceRecognition
import joblib
import pandas as pd
from google.cloud import firestore
from google.cloud import vision
from google.oauth2 import service_account
from datetime import datetime, timedelta
import spacy
import easyocr
import pytesseract
from PIL import Image
import numpy as np
import re
import traceback

app = FastAPI()
origins = [
    "http://localhost:3000",
    "http://localhost:3001"
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Load MOdels
decision_tree_model_for_dosage = joblib.load('drug_strength_model_dt.joblib')
label_encoders = joblib.load('label_encoders.joblib')
calorie_ex_model = joblib.load('calorie_exercise.joblib')

# Db connection
db = get_firestore_client()

class User(BaseModel):
    username: str
    role: str
    full_name: str
    email:str
    contact: str
    password: str
    nic: str
    speciality: Optional[str] = Field(default="No", description="Required only if role is 'doctor'")

class LoginUser(BaseModel):
    username: str
    password: str

class UserLog(BaseModel):
    username: str
    action: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    ip_address: str
    user_agent: str
    other_details: Optional[dict] = None

class FaceID(BaseModel):
    username: str

class Drug(BaseModel):
    drug_name: str
    category: str
    dosage_form: str
    indication: str
    classification: str


class PrescriptionItem(BaseModel):
    medication_name: str
    dosage: str
    frequency: List[str]
    duration: str

class PrescriptionRecord(BaseModel):
    user: str
    title: str
    details: Optional[str]
    prescriptions: List[PrescriptionItem]

class PrescriptionRetrieveRecord(BaseModel):
    user: str
    title: str
    details: Optional[str]
    prescriptions: List[PrescriptionItem]
    created_at: datetime = Field(default_factory=datetime.utcnow)

users_db = {}

def log_action(username: str, action: str, ip_address: str, user_agent: str, other_details: Optional[dict]):
    log_data = {
        "username": username,
        "action": action,
        "timestamp": datetime.utcnow(),
        "ip_address": ip_address,
        "user_agent": user_agent,
        "other_details": other_details,
    }
    db.collection("logs").add(log_data)

@app.post("/register")
async def register_user(user: User, request: Request):
    user_ref = db.collection("users").document(user.username)
    if user_ref.get().exists:
        raise HTTPException(status_code=400, detail="Username already registered")

    # Hash the password before storing it
    hashed_password = bcrypt.hashpw(user.password.encode('utf-8'), bcrypt.gensalt())
    user_data = user.dict()
    user_data["password"] = hashed_password.decode('utf-8')

    user_ref.set(user_data)

    # Log the action
    ip_address = request.client.host
    user_agent = request.headers.get("User-Agent")
    other_details = {
        "referer": request.headers.get("Referer"),
        "accept_language": request.headers.get("Accept-Language"),
    }

    # Log the action with network details
    log_action(user.username, "User Created account", ip_address, user_agent, other_details)

    return {"message": "User registered successfully", "user": user_data}

@app.post("/login")
async def login_user(user: LoginUser, request: Request):
    user_ref = db.collection("users").document(user.username)
    user_doc = user_ref.get()

    if not user_doc.exists:
        raise HTTPException(status_code=400, detail="Invalid username or password")

    user_data = user_doc.to_dict()
    
    # Check the hashed password
    if not bcrypt.checkpw(user.password.encode('utf-8'), user_data["password"].encode('utf-8')):
        raise HTTPException(status_code=400, detail="Invalid username or password")

    user_data.pop("password")  # Remove the password field from the response

    # Capture network details
    ip_address = request.client.host
    user_agent = request.headers.get("User-Agent")
    other_details = {
        "referer": request.headers.get("Referer"),
        "accept_language": request.headers.get("Accept-Language"),
    }

    # Log the action with network details
    log_action(user.username, "User logged in", ip_address, user_agent, other_details)

    return {"message": "Login successful", "user": user_data}

@app.get("/users/{username}")
async def get_user(username: str):
    user_ref = db.collection("users").document(username)
    user_snapshot = user_ref.get()
    
    if not user_snapshot.exists:
        raise HTTPException(status_code=404, detail="User not found")

    user_data = user_snapshot.to_dict()
    user_data.pop("password", None)  # Remove sensitive data

    # Determine avatar file path
    avatar_filename = f"{username}.jpg"
    avatar_path = os.path.join(UPLOAD_DIR, avatar_filename)

    if not os.path.exists(avatar_path):
        avatar_filename = f"{username}.png"
        avatar_path = os.path.join(UPLOAD_DIR, avatar_filename)

    if not os.path.exists(avatar_path):
        avatar_filename = "sample.png"  # Default avatar
        avatar_path = os.path.join(UPLOAD_DIR, avatar_filename)

    # Return JSON response with avatar URL
    return {
        "user": user_data,
        "avatar": f"/avatars/{avatar_filename}"  # Image URL
    }

@app.get("/avatars/{filename}")
async def get_avatar(filename: str):
    file_path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    
    return FileResponse(file_path)

@app.delete("/users/{username}")
async def delete_user(username: str, request: Request):
    user_ref = db.collection("users").document(username)
    user_snapshot = user_ref.get()

    if not user_snapshot.exists:
        raise HTTPException(status_code=404, detail="User not found")

    # Delete the user from Firestore
    user_ref.delete()

    # Log the action
    ip_address = request.client.host
    user_agent = request.headers.get("User-Agent")
    other_details = {
        "referer": request.headers.get("Referer"),
        "accept_language": request.headers.get("Accept-Language"),
    }
    log_action(username, "User deleted", ip_address, user_agent, other_details)

    return {"message": "User deleted successfully"}


@app.put("/users/{username}")
async def update_user(username: str, user_update: User):
    user_ref = db.collection("users").document(username)
    user_snapshot = user_ref.get()

    if not user_snapshot.exists:
        raise HTTPException(status_code=404, detail="User not found")

    # Convert the updated user data to a dictionary
    user_data = user_update.dict()

    # Retrieve the existing user data
    existing_user_data = user_snapshot.to_dict()

    # Preserve the existing hashed password if not changed
    if user_update.password:
        hashed_password = bcrypt.hashpw(user_update.password.encode('utf-8'), bcrypt.gensalt())
        user_data["password"] = hashed_password.decode('utf-8')
    else:
        user_data["password"] = existing_user_data["password"]

    # Update user document
    user_ref.update(user_data)

    # Log the action
    log_action(username, "User details updated")

    return {"message": "User updated successfully", "user": user_data}


@app.get("/users")
async def get_users(role: str = None):
    # Create a query to get users based on role or all users without role filtering
    users_ref = db.collection("users")
    if role:
        users_ref = users_ref.where("role", "==", role)

    users_snapshot = users_ref.stream()

    users_data = []
    for user in users_snapshot:
        user_data = user.to_dict()
        
        # Remove the password field from the returned data
        user_data.pop("password", None)
        
        users_data.append(user_data)

    return {"users": users_data}

database = {}

class PersonalHealth(BaseModel):
    user: str
    heart_diseases: bool = False
    heart_attack: bool = False
    cholesterol: int = 150  
    diabetes: bool = False
    blood_pressure: str = "120/80"
    bmi: Optional[float] = None
    allergies: Optional[str] = None
    last_checkup: Optional[datetime] = None
    age: Optional[int] = 20  
    gender: Optional[str] = "Male"

# Log structure
class LogEntry(BaseModel):
    timestamp: datetime
    action: str
    details: dict

logs = []

# Create or update personal health data
@app.post("/health")
def create_or_update_health(health: PersonalHealth):
    user_id = health.user
    user_ref = db.collection("personal_health").document(user_id)

    # Check if the document exists in Firestore
    doc = user_ref.get()
    
    if doc.exists:
        # Update existing record
        user_ref.update(health.dict())
        action = "Update"
    else:
        # Create a new record
        user_ref.set(health.dict())
        action = "Create"

    return {
        "message": f"Health record {action}d successfully",
        "data": health.dict()
    }

# Get health data by user
@app.get("/health/{user_id}")
def get_health(user_id: str):
    if user_id not in database:
        raise HTTPException(status_code=404, detail="User not found")
    
    logs.append(LogEntry(timestamp=datetime.now(), action="Retrieve", details={"user": user_id}))
    return database[user_id]

@app.get("/users/{username}/all")
async def get_user(username: str):
    user_ref = db.collection("users").document(username)
    user_snapshot = user_ref.get()

    if not user_snapshot.exists:
        raise HTTPException(status_code=404, detail="User not found")

    user_data = user_snapshot.to_dict()
    user_data.pop("password", None)  # Remove password field

    # Fetch personal health details
    health_ref = db.collection("personal_health").document(username)
    health_snapshot = health_ref.get()

    if not health_snapshot.exists:
        # Create new health document with default values
        default_health = PersonalHealth(user=username)
        health_ref.set(default_health.dict())
        health_data = default_health.dict()
    else:
        health_data = health_snapshot.to_dict()

    return {"user": user_data, "personal_health": health_data}

@app.get("/health-records/get-all")
async def get_all_users():
    users_ref = db.collection("users")
    users_snapshot = users_ref.stream()

    users_data = []
    
    for user_doc in users_snapshot:
        user_data = user_doc.to_dict()
        username = user_doc.id  # Get the document ID as the username

        user_data.pop("password", None)  # Remove password field

        # Fetch personal health details
        health_ref = db.collection("personal_health").document(username)
        health_snapshot = health_ref.get()

        if not health_snapshot.exists:
            # Create new health document with default values
            default_health = PersonalHealth(user=username)
            health_ref.set(default_health.dict())
            health_data = default_health.dict()
        else:
            health_data = health_snapshot.to_dict()

        users_data.append({"user": user_data, "personal_health": health_data})

    return {"users": users_data}

@app.get("/user/{username}/logs")
async def get_user_logs(username: str):
    # Query for user logs based on username
    logs_ref = db.collection("logs").where("username", "==", username)
    logs_snapshot = logs_ref.stream()

    logs_data = []
    for log in logs_snapshot:
        log_data = log.to_dict()
        logs_data.append(log_data)

    if not logs_data:
        raise HTTPException(status_code=404, detail="No logs found for this user")

    return {"logs": logs_data}



# Checkup Health risk
MODEL_RISK = joblib.load("random_forest_helth_risk_pred.joblib")

gender_encoding = {"Female": 0, "Male": 1, "O": 2}

# Encoding for Family History
family_history_encoding = {"No": 0, "Yes": 1}

# Define request schema
class PatientData(BaseModel):
    age: float
    gender: str
    family_history: str
    systolic_bp: float
    diastolic_bp: float
    heart_rate: float

@app.post("/predict-health-risk")
def predict_disease_risk(data: PatientData):
    

    # Encode categorical values
    gender_encoded = gender_encoding.get(data.gender, -1)
    family_history_encoded = family_history_encoding.get(data.family_history, -1)

    # Ensure valid encoding
    if gender_encoded == -1 or family_history_encoded == -1:
        return {"error": "Invalid input for gender or family history!"}

    # Prepare input for model
    input_data = np.array([[data.age, gender_encoded, family_history_encoded, data.systolic_bp, data.diastolic_bp, data.heart_rate]])

    # Make prediction
    predicted_risk = MODEL_RISK.predict(input_data)[0]

    return {"Predicted Disease Risk (%)": round(predicted_risk, 2)}


# Medicines
class Medicine(BaseModel):
    name: str
    dosage: str
    interval: List[str]  # Store as a list of strings
    days: str
    isPopular: Optional[bool] = False
    schedule: Optional[List[str]] = []  # New field to store the schedule

class PrescriptionSchedule(BaseModel):
    user: str  # Username or user ID
    medicines: List[Medicine]
    date_created: Optional[datetime] = datetime.utcnow()

# Helper function to clean 'days' field and convert it to a number
def clean_days(days: str) -> int:
    # Extract numeric value from the string (e.g., "20 days" → 20)
    match = re.search(r'(\d+)', days)
    return int(match.group(1)) if match else 0

# Helper function to clean 'dosage' field and convert it to a number
def clean_dosage(dosage: str) -> int:
    # Extract numeric value from the string (e.g., "250mg" → 250)
    match = re.search(r'(\d+)', dosage)
    return int(match.group(1)) if match else 0

# Helper function to decode the 'interval' and return a list of schedule times
def decode_interval(interval_list: List[str]) -> List[str]:
    # Times for each part of the day
    time_dict = {
        0: '8:00 AM',   # Morning
        1: '1:00 PM',   # Noon
        2: '9:00 PM'    # Evening
    }
    
    schedule = []

    for interval in interval_list:
        # Extract the numeric pattern (e.g., "1-0-0" from "1-0-0 before meal")
        match = re.search(r'\b[01]-[01]-[01]\b', interval)
        if match:
            pattern = match.group()  # Extract matched pattern
            parts = pattern.split('-')  # Convert to list
            
            # Decode the pattern into time slots
            for idx, val in enumerate(parts):
                if val == '1':  
                    schedule.append(time_dict[idx])  # Add corresponding time

    return schedule

@app.post("/prescriptionSchedule")
async def save_prescription(schedule: PrescriptionSchedule):
    try:
        # Process the medicines to clean 'days', 'dosage', and decode 'interval' before saving
        for medicine in schedule.medicines:
            medicine.days = clean_days(medicine.days)  # Clean the 'days' field
            medicine.dosage = clean_dosage(medicine.dosage)  # Clean the 'dosage' field
            medicine.schedule = decode_interval(medicine.interval)  # Decode 'interval' to get schedule times

        # Firestore reference to "prescription_schedules" collection
        prescription_ref = db.collection("prescription_schedules").document()

        # Convert Pydantic model to dictionary
        schedule_data = schedule.dict()

        # Convert date_created to Firestore-compatible format
        schedule_data["date_created"] = datetime.utcnow().isoformat()

        # Store in Firestore
        prescription_ref.set(schedule_data)

        return {
            "message": "Prescription schedule saved successfully!",
            "data": schedule_data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/prescriptionSchedule/{user}")
async def get_prescriptions_by_user(user: str):
    try:
        prescriptions_ref = db.collection("prescription_schedules") \
            .where("user", "==", user) \
            .order_by("date_created", direction=firestore.Query.DESCENDING)
        
        docs = prescriptions_ref.stream()
        prescriptions = [doc.to_dict() | {"id": doc.id} for doc in docs]

        if not prescriptions:
            raise HTTPException(status_code=404, detail="No prescriptions found for this user")

        return {"user": user, "prescriptions": prescriptions}
    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"Error: {e}\nTraceback:\n{error_trace}")
        raise HTTPException(status_code=500, detail=str(e))

# **2. Update a prescription schedule by ID**
@app.put("/prescriptionSchedule/{prescription_id}")
async def update_prescription(prescription_id: str, schedule: PrescriptionSchedule):
    try:
        prescription_ref = db.collection("prescription_schedules").document(prescription_id)

        if not prescription_ref.get().exists:
            raise HTTPException(status_code=404, detail="Prescription not found")

        schedule_data = schedule.dict()
        schedule_data["date_created"] = datetime.utcnow().isoformat()

        prescription_ref.update(schedule_data)

        return {"message": "Prescription updated successfully", "data": schedule_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# **3. Delete a prescription schedule by ID**
@app.delete("/prescriptionSchedule/{prescription_id}")
async def delete_prescription(prescription_id: str):
    try:
        prescription_ref = db.collection("prescription_schedules").document(prescription_id)

        if not prescription_ref.get().exists:
            raise HTTPException(status_code=404, detail="Prescription not found")

        prescription_ref.delete()

        return {"message": "Prescription deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Medicine Intakes 
class MedicineIntake(BaseModel):
    medicine: str
    time: str  # Example: "9:00 AM"

class MedicineIntakeRecord(BaseModel):
    date: str  # YYYY-MM-DD
    takings: List[MedicineIntake]

@app.post("/prescriptionSchedule/{schedule_id}/medicineIntake")
async def record_medicine_intake(schedule_id: str, intake_record: MedicineIntakeRecord):
    try:
        intake_ref = (
            db.collection("prescription_schedules")
            .document(schedule_id)
            .collection("medicine_intake")
            .document(intake_record.date)
        )
        
        intake_ref.set({"date": intake_record.date, "takings": [taking.dict() for taking in intake_record.takings]})
        
        return {"message": "Medicine intake recorded successfully!", "data": intake_record.dict()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/prescriptionSchedule/{schedule_id}/medicineIntake/{date_id}")
async def get_medicine_intake(schedule_id: str, date_id: str):
    try:
        intake_ref = (
            db.collection("prescription_schedules")
            .document(schedule_id)
            .collection("medicine_intake")
            .document(date_id)
        )
        doc = intake_ref.get()
        
        if not doc.exists:
            raise HTTPException(status_code=404, detail="No record found for this date")
        
        return doc.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/prescriptionSchedule/{schedule_id}/medicineIntake/{date_id}")
async def update_medicine_intake(schedule_id: str, date_id: str, intake_record: MedicineIntakeRecord):
    try:
        intake_ref = (
            db.collection("prescription_schedules")
            .document(schedule_id)
            .collection("medicine_intake")
            .document(date_id)
        )
        
        if not intake_ref.get().exists:
            raise HTTPException(status_code=404, detail="No record found for this date")
        
        intake_ref.update({"takings": [taking.dict() for taking in intake_record.takings]})
        
        return {"message": "Medicine intake updated successfully!", "data": intake_record.dict()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/prescriptionSchedule/{schedule_id}/medicineIntake/{date_id}")
async def delete_medicine_intake(schedule_id: str, date_id: str):
    try:
        intake_ref = (
            db.collection("prescription_schedules")
            .document(schedule_id)
            .collection("medicine_intake")
            .document(date_id)
        )
        
        if not intake_ref.get().exists:
            raise HTTPException(status_code=404, detail="No record found for this date")
        
        intake_ref.delete()
        
        return {"message": "Medicine intake deleted successfully!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/prescriptionSchedule/{schedule_id}/medicineIntake")
async def get_all_medicine_intake(schedule_id: str):
    try:
        intake_ref = (
            db.collection("prescription_schedules")
            .document(schedule_id)
            .collection("medicine_intake")
        )
        docs = intake_ref.stream()
        
        records = [doc.to_dict() | {"id": doc.id} for doc in docs]
        
        return {"schedule_id": schedule_id, "intake_records": records}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Face Detction
@app.post("/face-detection/upload")
async def upload_face(file: UploadFile = File(...)):
    file_location = os.path.join(UPLOAD_DIR, file.filename)
    with open(file_location, "wb+") as file_object:
        shutil.copyfileobj(file.file, file_object)
    return {"info": "File uploaded successfully"}

@app.post("/face-detection/recognize")
async def recognize_face(user: FaceID):
    name = user.username
    face_rec = FaceRecognition()
    detected = face_rec.run_recognition(name)  
    print(detected)
    return {"detected": detected}
    # if detected:
    #     return JSONResponse(status_code=200, content={"message": "Face recognized"})
    # else:
    #     raise HTTPException(status_code=404, detail="Face not recognized")

def predict_drug_strength(drug_name, category, dosage_form, indication, classification):
    # Create a dictionary for the input
    input_data = {
        'Name': drug_name,
        'Category': category,
        'Dosage Form': dosage_form,
        'Indication': indication,
        'Classification': classification
    }

    # Encode the input data using the label encoders
    for col in input_data:
        if col in label_encoders:
            input_data[col] = label_encoders[col].transform([input_data[col]])[0]

    # Convert the input into a DataFrame to match the model's expected input format
    input_df = pd.DataFrame([input_data])

    # Predict the strength using the loaded Decision Tree model
    predicted_strength = decision_tree_model_for_dosage.predict(input_df)[0]

    return predicted_strength

@app.post("/medicine-suggetion-dosage")
async def get_dosage(user: Drug):
    # Call the prediction function with the input from the client
    prediction = predict_drug_strength(
        drug_name=user.drug_name,
        category=user.category,
        dosage_form=user.dosage_form,
        indication=user.indication,
        classification=user.classification
    )
    
    return {"message": "Prediction successful", "dosage": prediction}

class ExerciseRequest(BaseModel):
    exerciseName: str

@app.post("/start-exercise")
async def start_exercise(request: ExerciseRequest):
    try:
        exe_launch(request.exerciseName)  # Pass exercise name dynamically
        return JSONResponse(content={"message": f"Exercise '{request.exerciseName}' started!"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class CaloriePredictionInput(BaseModel):
    age: int
    gender: str
    weight: float
    height: float
    max_bpm: int
    avg_bpm: int
    resting_bpm: int
    session_duration: float
    workout_type: str
    fat_percentage: float
    water_intake: float
    workout_frequency: int
    experience_level: int
    bmi: float

# Define label mappings
gender_mapping = {'Female': 0, 'Male': 1}
workout_type_mapping = {'Cardio': 0, 'HIIT': 1, 'Strength': 2, 'Yoga': 3}

def predict_calories_burned(age, gender, weight, height, max_bpm, avg_bpm, resting_bpm, session_duration,
                            workout_type, fat_percentage, water_intake, workout_frequency, experience_level, bmi):
    """
    Predict calories burned based on input features using the trained model.
    
    Parameters:
        - age (int): Age of the individual
        - gender (str): Gender ('Female' or 'Male')
        - weight (float): Weight in kg
        - height (float): Height in meters
        - max_bpm (int): Maximum BPM
        - avg_bpm (int): Average BPM
        - resting_bpm (int): Resting BPM
        - session_duration (float): Session duration in hours
        - workout_type (str): Workout type ('Cardio', 'HIIT', 'Strength', 'Yoga')
        - fat_percentage (float): Body fat percentage
        - water_intake (float): Water intake in liters
        - workout_frequency (int): Workout frequency (days/week)
        - experience_level (int): Experience level (e.g., 0 for beginner, 1 for intermediate, etc.)
        - bmi (float): BMI
        
    Returns:
        - Predicted calories burned (float)
    """
    # Map categorical values
    gender_encoded = gender_mapping.get(gender)
    workout_type_encoded = workout_type_mapping.get(workout_type)
    
    # Check if mappings were successful
    if gender_encoded is None or workout_type_encoded is None:
        raise ValueError("Invalid categorical input. Please check 'gender' or 'workout_type' values.")
    
    # Construct input array
    input_features = np.array([[age, gender_encoded, weight, height, max_bpm, avg_bpm, resting_bpm,
                                 session_duration, workout_type_encoded, fat_percentage, water_intake,
                                 workout_frequency, experience_level, bmi]])
    
    # Predict using the trained model
    prediction = calorie_ex_model.predict(input_features)
    return prediction[0]

@app.post("/calories/predict")
async def predict_calories(input_data: CaloriePredictionInput):
    try:
        # Map categorical values
        gender_encoded = gender_mapping.get(input_data.gender)
        workout_type_encoded = workout_type_mapping.get(input_data.workout_type)

        if gender_encoded is None or workout_type_encoded is None:
            raise ValueError("Invalid categorical input. Check 'gender' or 'workout_type' values.")

        # Prepare input for the prediction function
        prediction = predict_calories_burned(
            age=input_data.age,
            gender=input_data.gender,
            weight=input_data.weight,
            height=input_data.height,
            max_bpm=input_data.max_bpm,
            avg_bpm=input_data.avg_bpm,
            resting_bpm=input_data.resting_bpm,
            session_duration=input_data.session_duration,
            workout_type=input_data.workout_type,
            fat_percentage=input_data.fat_percentage,
            water_intake=input_data.water_intake,
            workout_frequency=input_data.workout_frequency,
            experience_level=input_data.experience_level,
            bmi=input_data.bmi
        )

        return {"predicted_calories_burned": prediction}

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Drug Addherence 
@app.post("/prescription/create")
async def create_prescription(record: PrescriptionRecord):
    prescription_ref = db.collection("prescriptions").document()
    prescription_data = record.dict()
    prescription_data["created_at"] = firestore.SERVER_TIMESTAMP
    prescription_ref.set(prescription_data)
    return {"message": "Prescription record created successfully", "record_id": prescription_ref.id}

@app.get("/prescription/{user}")
async def get_prescriptions_by_user(user: str):
    try:
        # Query the prescriptions collection for the given user
        prescriptions = db.collection("prescriptions").where("user", "==", user).stream()

        # Add the document ID to each prescription dictionary
        prescription_list = [
            {**doc.to_dict(), "id": doc.id} for doc in prescriptions
        ]

        # Return the result
        return {"message": "Prescriptions retrieved successfully", "prescriptions": prescription_list}

    except Exception as e:
        # Handle any errors during the process
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/prescription/update/{record_id}")
async def update_prescription(record_id: str, record: PrescriptionRetrieveRecord):
    prescription_ref = db.collection("prescriptions").document(record_id)
    if not prescription_ref.get().exists:
        raise HTTPException(status_code=404, detail="Prescription record not found")

    prescription_data = record.dict()
    prescription_ref.update(prescription_data)
    return {"message": "Prescription record updated successfully"}


# Drug Adherence
nlp = spacy.load('en_core_web_sm')
reader = easyocr.Reader(['en'])  

class PrescriptionParsedInfo(BaseModel):
    recognized_text: str
    parsed_prescription_info: Dict[str, Any]

@app.post("/api/parse-prescription")
async def recognize_and_parse_prescription(file: UploadFile = File(...)):
    try:
        file_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(file_path, "wb") as buffer:
            buffer.write(await file.read())

        result = reader.readtext(file_path)
        recognized_text = "\n".join([text[1] for text in result])
        print(recognized_text)
        parsed_info = parse_prescription(recognized_text)

        return {
            "recognized_text": recognized_text,
            "parsed_prescription_info": parsed_info
        }

    except Exception as e:
        return {"error": f"Error: {str(e)}"}
    
@app.post("/api/parse-prescription-tesseract")
async def recognize_and_parse_prescription(file: UploadFile = File(...)):
    try:
        file_path = os.path.join(UPLOAD_DIR, file.filename)
        os.makedirs(UPLOAD_DIR, exist_ok=True)  # Ensure upload directory exists

        with open(file_path, "wb") as buffer:
            buffer.write(await file.read())

        # Open and OCR process the image
        image = Image.open(file_path)
        recognized_text = pytesseract.image_to_string(image)

        # Print recognized text for debugging
        print("Recognized Text:", recognized_text)

        # Parse prescription details
        parsed_info = parse_prescription(recognized_text)

        # Generate drug schedule from parsed info
        schedule = generate_schedule(parsed_info)

        return {
            "recognized_text": recognized_text,
            "parsed_prescription_info": parsed_info,
            "schedule": [time.strftime("%Y-%m-%d %H:%M:%S") for time in schedule],
        }

    except Exception as e:
        return {"error": f"Error: {str(e)}"}

def parse_prescription(text: str) -> List[Tuple[str, str]]:
    """
    Parse prescription details from the text using SpaCy.
    Returns a list of tuples containing the extracted entities.
    """
    doc = nlp(text)
    extracted_entities = []
    for ent in doc.ents:
        if ent.label_ in ("DRUG", "QUANTITY", "TIME"):
            extracted_entities.append((ent.text, ent.label_))
    return extracted_entities

def generate_schedule(parsed_info: List[Tuple[str, str]]) -> List[datetime]:
    """
    Generate a schedule for drug administration based on TIME values.
    """
    schedule = []
    now = datetime.now()

    for drug, label in parsed_info:
        if label == "TIME":
            try:
                # Extract interval (e.g., 'every 6 hours' -> 6)
                interval = int(drug.split()[1])  # Assume format 'every <n> hours'
                for i in range(4):  # Generate schedule for 4 intervals
                    schedule.append(now + timedelta(hours=interval * i))
            except (ValueError, IndexError) as e:
                print(f"Error parsing TIME value '{drug}': {e}")
    return schedule

@app.post("/api/parse-prescription-google")
async def recognize_and_parse_prescription(file: UploadFile = File(...)):
    try:
        # Step 1: Save the uploaded file
        file_path = f"{UPLOAD_DIR}/{file.filename}"
        with open(file_path, "wb") as buffer:
            buffer.write(await file.read())

        # Step 2: Initialize Google Cloud Vision client for OCR
        credentials = service_account.Credentials.from_service_account_file('addproadb-18f9d6a0f7ad.json')
        client = vision.ImageAnnotatorClient(credentials=credentials)

        # Load the image into memory
        with open(file_path, "rb") as image_file:
            content = image_file.read()

        image = vision.Image(content=content)

        # Perform text detection
        response = client.text_detection(image=image)
        texts = response.text_annotations

        if response.error.message:
            raise Exception(f'{response.error.message}')

        # Get the recognized text
        recognized_text = texts[0].description if texts else ""

        # Step 3: Parse the recognized text using SpaCy for prescription details
        parsed_info = parse_prescription(recognized_text)
        cleaned_text = " ".join(recognized_text.splitlines())
        print(cleaned_text)

        # Step 4: Return the recognized text and parsed prescription details
        return {
            "recognized_text": cleaned_text,
            "parsed_prescription_info": parsed_info
        }

    except Exception as e:
        return {"error": f"Error: {str(e)}"}
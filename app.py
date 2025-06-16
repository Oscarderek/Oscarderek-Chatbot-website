from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from transformers import pipeline
import fitz  # PyMuPDF for PDF extraction
from docx import Document  # For DOCX extraction
import os
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# ✅ Load a lightweight model to avoid performance issues
model_name = "distilbert-base-cased-distilled-squad"
qa_pipeline = pipeline("question-answering", model=model_name, device=-1)  # Use CPU (-1) to avoid GPU errors

# ✅ Flask App Configuration
app = Flask(__name__)
app.secret_key = "your_secret_key"  # Change this to a secure key
app.config["UPLOAD_FOLDER"] = "uploads"
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # Limit file size to 10MB

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///oscardata.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ✅ Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# ✅ Ensure necessary directories exist
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


# ✅ Flask-Login User class
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    Username = db.Column(db.String(64), unique=True, nullable=False)
    Email = db.Column(db.String(128), unique=True, nullable=False)
    pwd = db.Column(db.String(128), nullable=False)

class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  # Links message to a user
    message = db.Column(db.Text, nullable=False)
    sender = db.Column(db.String(10), nullable=False)  # "user" or "bot"
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())

    user = db.relationship('User', backref=db.backref('messages', lazy=True))  # Optional relationship

    def __repr__(self):
        return f"<ChatMessage {self.message[:50]}...>"



@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ✅ Allowed file extensions
ALLOWED_EXTENSIONS = {"pdf", "docx", "txt"}
DEFAULT_CONTEXT = "Upload a document to use it as context for answering questions."


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/")
def index():
    return render_template("main.html")


@app.route("/dashboard")
@login_required  # Ensure only logged-in users can access
def dashboard():
    return render_template("dashboard.html", username=current_user.Username)


@app.route("/main")
@login_required  # Ensure only logged-in users can access
def main():
    return render_template("main.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        user = User.query.filter_by(Email=email).first()

        if user and check_password_hash(user.pwd, password):
            login_user(user)
            flash("Login Successful!", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid username or password", "danger")
            return redirect(url_for("login"))

    return render_template("login.html")

@app.route("/chatbot")
def chatbot():
    return render_template("chat.html", username=current_user.Username)

  
@app.route("/get_username")
@login_required
def get_username():
    return jsonify({"username": current_user.Username})


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logout successful!", "success")
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form["username"]
        email = request.form["email"]
        password = request.form["password"]

        if User.query.filter_by(Email=email).first() or User.query.filter_by(Username=username).first():
            flash("User already exists", "danger")
            return redirect(url_for("signup"))

        hashed_password = generate_password_hash(password)  # Hash only once
        new_user = User(Username=username, Email=email, pwd=hashed_password)
        db.session.add(new_user)
        db.session.commit()

        flash("Registration Successful! Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("signup.html")

@app.route("/chat_history", methods=["GET"])
@login_required
def chat_history():
    """Fetch chat history for the logged-in user."""
    messages = ChatMessage.query.filter_by(user_id=current_user.id).order_by(ChatMessage.timestamp).all()

    chat_data = [{"message": msg.message, "sender": msg.sender, "timestamp": msg.timestamp.strftime("%Y-%m-%d %H:%M:%S")}
                 for msg in messages]

    return jsonify({"chat_history": chat_data})  # ✅ Return as dictionary





@app.route("/upload", methods=["POST"])
@login_required
def upload_file():
    global DEFAULT_CONTEXT

    # Check if a file was uploaded
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]

    # Validate file name and type
    if file.filename == "" or not allowed_file(file.filename):
        return jsonify({"error": "Invalid file type. Allowed: PDF, DOCX, TXT"}), 400

    # Check file size
    if file.content_length and file.content_length > app.config["MAX_CONTENT_LENGTH"]:
        return jsonify({"error": "File size exceeds 10MB"}), 400

    # Save the file with its original name
    file_path = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
    file.save(file_path)

    # Extract text from file
    text = extract_text(file_path)
    # print(text)
    if text:
        DEFAULT_CONTEXT = text
        # return jsonify({"message": f"File uploaded successfully: {file.filename}"}), 200
        return "", 204
    else:
        return jsonify({"error": "Could not extract text from file"}), 400


def extract_text(file_path):
    """Extracts text from different file formats."""
    try:
        if file_path.endswith(".pdf"):
            return extract_text_from_pdf(file_path)
        elif file_path.endswith(".docx"):
            return extract_text_from_docx(file_path)
        elif file_path.endswith(".txt"):
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        else:
            return None
    except Exception as e:
        print(f"Error extracting text: {e}")
        return None


def extract_text_from_pdf(file_path):
    """Extracts text from a PDF file efficiently."""
    text = []
    doc = fitz.open(file_path)
    for page in doc:
        text.append(page.get_text("text"))
    return "\n".join(text).strip()


def extract_text_from_docx(file_path):
    """Extracts text from a DOCX file."""
    doc = Document(file_path)
    return "\n".join([para.text for para in doc.paragraphs]).strip()


@app.route("/qa", methods=["POST"])
@login_required
def question_answer():
    """Handles question-answering."""
    try:
        data = request.get_json()
        question = data.get("question", "").strip()

        if not question:
            return jsonify({"error": "No question provided"}), 400

        if len(DEFAULT_CONTEXT) < 10:
            return jsonify({"error": "Please upload a document first"}), 400

        print("Processing question:", question)
        result = qa_pipeline({"context": DEFAULT_CONTEXT, "question": question}, max_answer_len=500)

        print("Raw Model Output:", result)

        # Ensure a proper answer is returned
        if not result or "answer" not in result or result["answer"].strip() == "":
            return jsonify({"error": "Sorry, I couldn't find an answer in the document."}), 400

        # Extract answer and score
        answer = result["answer"].strip()
        score = round(result["score"], 4)

        # ✅ Ensure the answer starts with "Yes" if needed
        if question.lower().startswith("is") or question.lower().startswith("are") or question.lower().startswith("do"):
            answer = "Yes, " + answer if "yes" not in answer.lower() else answer

        # ✅ Save user's question to the database
        user_message = ChatMessage(user_id=current_user.id, message=question, sender="user")
        db.session.add(user_message)

        # ✅ Save bot's answer to the database
        bot_message = ChatMessage(user_id=current_user.id, message=answer, sender="bot")
        db.session.add(bot_message)

        # ✅ Commit changes to the database
        db.session.commit()

        return jsonify(answer=answer, score=score)

    except Exception as e:
        print(f"Error processing question: {e}")
        return jsonify({"error": "An unexpected error occurred. Please try again later."}), 500




if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # Ensure database tables exist
    app.run(debug=True)

import os
import google.generativeai as genai
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import PyPDF2
import re
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from heapq import nlargest

# Load environment variables
load_dotenv()

# Configure Gemini API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

generation_config = {
    "temperature": 1,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 8192,
    "response_mime_type": "text/plain",
}

model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    generation_config=generation_config,
)

chat_session = model.start_chat(history=[])

# Flask app setup
app = Flask(__name__)
CORS(app)

# Global variables
pdf_texts = {}
file_details = []
user_query_history = {}

# Directory containing your files
FILES_DIRECTORY = 'files'  # Change this to your local directory path

# Load all files automatically during app initialization
def load_all_files_on_startup():
    global pdf_texts, file_details
    file_details = []

    for root, dirs, files in os.walk(FILES_DIRECTORY):
        for file_name in files:
            file_path = os.path.join(root, file_name)
            mime_type = 'application/pdf' if file_name.endswith('.pdf') else 'text/plain' if file_name.endswith('.txt') else None
            if mime_type:
                file_details.append({
                    "file_name": file_name,
                    "file_path": file_path,
                    "mime_type": mime_type
                })

                try:
                    if mime_type == 'application/pdf':
                        file_text = extract_text_from_pdf(file_path)
                    elif mime_type == 'text/plain':
                        file_text = extract_text_from_text_file(file_path)
                    else:
                        continue

                    pdf_texts[file_path] = file_text
                except Exception as e:
                    print(f"Error processing file {file_name}: {e}")

# Extract text from PDF file
def extract_text_from_pdf(file_path):
    with open(file_path, 'rb') as f:
        pdf_reader = PyPDF2.PdfReader(f)
        text = ""
        for page_num in range(len(pdf_reader.pages)):
            page = pdf_reader.pages[page_num]
            text += page.extract_text() or ""
    return text

# Extract text from text file
def extract_text_from_text_file(file_path):
    with open(file_path, 'r') as f:
        text = f.read()
    return text

# Rank documents based on relevance to user query
def rank_documents(query):
    if not pdf_texts:
        raise ValueError("No documents found to rank against. Ensure documents are loaded correctly.")
    
    all_texts = list(pdf_texts.values())
    
    if not query.strip():
        raise ValueError("Query is empty or contains only whitespace.")
    
    tfidf_vectorizer = TfidfVectorizer(stop_words='english')
    tfidf_matrix = tfidf_vectorizer.fit_transform(all_texts + [query])
    
    if tfidf_matrix.shape[0] < 2:
        raise ValueError("There are no valid documents to compare the query against.")
    
    cosine_sim = cosine_similarity(tfidf_matrix[-1], tfidf_matrix[:-1])
    ranked_docs = cosine_sim.flatten().argsort()[::-1]
    
    return nlargest(3, ranked_docs, key=lambda idx: cosine_sim.flatten()[idx])

# Generate concise follow-up questions based on the most recent relevant content
def generate_follow_up_questions(relevant_text, previous_questions):
    follow_up_questions = []

    relevant_text = re.sub(r'(\d+)[,;](\d+)', r'\1 \2', relevant_text)
    relevant_text = re.sub(r'\s+', ' ', relevant_text)

    sentences = re.split(r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?|\!)\s', relevant_text)

    for sentence in sentences:
        sentence = sentence.strip()

        if len(sentence) > 20:
            words = sentence.split()
            question = "What about " + " ".join(words[:5]) + "?"
            
            if question not in previous_questions:
                follow_up_questions.append(question)
        
        if len(follow_up_questions) >= 3:
            break
    
    return follow_up_questions

# Chatbot response logic with document selection
def chatbot_respond(user_query, session_id):
    try:
        ranked_docs = rank_documents(user_query)
        relevant_text = ""

        for idx in ranked_docs:
            relevant_text += list(pdf_texts.values())[idx] + "\n"

        previous_questions = user_query_history.get(session_id, {}).get("follow_up_questions", [])

        follow_up_questions = generate_follow_up_questions(relevant_text, previous_questions)

        user_query_history[session_id] = {
            "query": user_query,
            "follow_up_questions": follow_up_questions,
            "relevant_text": relevant_text
        }

        system_prompt = f"""
       You are a knowledgeable assistant. Your role is to provide accurate and concise responses based only on the information in the provided documents.
        User's Question: "{user_query}"
        Relevant Context from Documents:
        {relevant_text}
        Answer the user's question in a professional tone, using no more than 100 words. Do not include any information that is not found in the documents.
        """

        response = chat_session.send_message(system_prompt)
        return response.text.strip(), follow_up_questions

    except Exception as e:
        return f"Error processing your query: {str(e)}", []

# Flask routes
@app.route('/chat', methods=['POST'])
def chat():
    user_query = request.json.get("query")
    session_id = request.json.get("session_id", "default")

    if not user_query:
        return jsonify({"error": "Query is required"}), 400

    bot_response, follow_up_questions = chatbot_respond(user_query, session_id)
    return jsonify({"response": bot_response, "follow_up_questions": follow_up_questions})

if __name__ == '__main__':
    # Load files automatically during app startup
    load_all_files_on_startup()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)

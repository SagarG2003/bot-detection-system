# 1. Data Collection and Preprocessing
import pandas as pd
import numpy as np
import re
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, roc_auc_score, accuracy_score, precision_score, recall_score, f1_score
from textblob import TextBlob
from datetime import datetime
from pyspark.sql import SparkSession
from kafka import KafkaConsumer
import json
from fastapi import FastAPI, Request
from cryptography.fernet import Fernet

# Loading the Twitter Bot Detection dataset
data = pd.read_csv('bot_detection_dataset.csv')

# Basic preprocessing
def clean_text(text):
    text = re.sub(r'http\S+', '', text)
    text = re.sub(r'@\w+', '', text)
    text = re.sub(r'#\w+', '', text)
    text = re.sub(r'[^A-Za-z\s]', '', text)
    return text.lower()

data['clean_text'] = data['text'].apply(clean_text)

# Text-based Features
# TF-IDF
tfidf = TfidfVectorizer(max_features=500)
tfidf_features = tfidf.fit_transform(data['clean_text']).toarray()

# Sentiment Analysis
data['sentiment'] = data['clean_text'].apply(lambda x: TextBlob(x).sentiment.polarity)

# Posting Patterns
# Time intervals between posts
data['timestamp'] = pd.to_datetime(data['timestamp'])
data['time_diff'] = data.groupby('user_id')['timestamp'].diff().fillna(pd.Timedelta(seconds=0)).dt.total_seconds()

# Volume of posts
post_volume = data.groupby('user_id')['text'].count().reset_index(name='post_volume')
data = data.merge(post_volume, on='user_id', how='left')

# Hashtag usage
data['hashtag_count'] = data['text'].apply(lambda x: len(re.findall(r'#\w+', x)))

# Engagement Metrics
data['likes'] = data['likes_count']
data['shares'] = data['shares_count']
data['mentions_count'] = data['text'].apply(lambda x: len(re.findall(r'@\w+', x)))

# Combining Features
features = np.hstack((
    tfidf_features,
    data[['sentiment', 'followers_to_following_ratio', 'time_diff', 'post_volume', 'hashtag_count', 'likes', 'shares', 'mentions_count']].fillna(0).values
))

# Labels (1 for bot, 0 for human)
labels = data['bot']

# Train-test split
X_train, X_test, y_train, y_test = train_test_split(features, labels, test_size=0.2, random_state=42)

# Model Development
model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train, y_train)

# Evaluation
y_pred = model.predict(X_test)
print("Accuracy:", accuracy_score(y_test, y_pred))
print("Precision:", precision_score(y_test, y_pred))
print("Recall:", recall_score(y_test, y_pred))
print("F1 Score:", f1_score(y_test, y_pred))
print("AUC-ROC:", roc_auc_score(y_test, y_pred))

# Scalability with Apache Spark
spark = SparkSession.builder.appName('BotDetection').getOrCreate()
spark_df = spark.createDataFrame(data)
spark_df = spark_df.withColumn('text_length_flag', spark_df['clean_text'].rlike('.{100,}').cast('int'))

# Real-Time Detection Pipeline
consumer = KafkaConsumer(
    'social_media_stream',
    bootstrap_servers=['localhost:9092'],
    value_deserializer=lambda x: json.loads(x.decode('utf-8'))
)

def process_stream():
    for message in consumer:
        post_data = message.value
        clean_post = clean_text(post_data['text'])
        tfidf_feature = tfidf.transform([clean_post]).toarray()
        sentiment = TextBlob(clean_post).sentiment.polarity
        followers_to_following_ratio = post_data['followers_count'] / (post_data['following_count'] + 1)
        hashtag_count = len(re.findall(r'#\w+', post_data['text']))
        mentions_count = len(re.findall(r'@\w+', post_data['text']))

        features = np.hstack((
            tfidf_feature,
            [[sentiment, followers_to_following_ratio, hashtag_count, mentions_count]]
        ))

        prediction = model.predict(features)
        confidence = model.predict_proba(features)[0, 1]
        print(f"User: {post_data['username']}, Bot Prediction: {prediction[0]}, Confidence: {confidence:.2f}")

# Reporting and Insights Generation
def generate_report(predictions, data):
    report = pd.DataFrame({
        'username': data['username'],
        'prediction': predictions,
        'confidence_score': model.predict_proba(features)[:, 1]
    })
    return report

report = generate_report(y_pred, data)
report.to_csv('detection_report.csv', index=False)

# Deployment and Security Measures
app = FastAPI()
key = Fernet.generate_key()
cipher_suite = Fernet(key)

@app.post("/predict")
async def predict(request: Request):
    req_data = await request.json()
    clean_post = clean_text(req_data['text'])
    tfidf_feature = tfidf.transform([clean_post]).toarray()
    sentiment = TextBlob(clean_post).sentiment.polarity
    followers_to_following_ratio = req_data['followers_count'] / (req_data['following_count'] + 1)
    hashtag_count = len(re.findall(r'#\w+', req_data['text']))
    mentions_count = len(re.findall(r'@\w+', req_data['text']))

    features = np.hstack((
        tfidf_feature,
        [[sentiment, followers_to_following_ratio, hashtag_count, mentions_count]]
    ))

    prediction = model.predict(features)[0]
    confidence = model.predict_proba(features)[0, 1]
    anonymized_username = cipher_suite.encrypt(req_data['username'].encode()).decode()

    return {"username": anonymized_username, "prediction": int(prediction), "confidence": float(confidence)}

@app.get("/report")
async def get_report():
    report = pd.read_csv('detection_report.csv')
    return report.to_dict(orient='records')

# Run API with: uvicorn bot_detection_system:app --reload

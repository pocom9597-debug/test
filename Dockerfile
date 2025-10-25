FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# (تأكد من أن اسم الملف هنا يطابق اسم ملفك)
CMD ["python", "app.py"]

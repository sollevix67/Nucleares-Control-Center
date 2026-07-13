FROM python:3.12-slim
WORKDIR /app
COPY app.py config.json ./
COPY static ./static
RUN mkdir -p /app/data
EXPOSE 8790
CMD ["python", "app.py", "--no-browser", "--host", "0.0.0.0"]

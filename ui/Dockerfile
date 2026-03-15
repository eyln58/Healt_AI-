FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
EXPOSE 8501
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "8501"]

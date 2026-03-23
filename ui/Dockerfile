# Alpine KALDIRILDI (çok önemli)
FROM node:20

WORKDIR /app

# Dependency cache için
COPY package.json package-lock.json* ./

# Rollup fix
RUN npm install --include=optional

# Kodları kopyala
COPY . .

EXPOSE 8501

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "8501"]
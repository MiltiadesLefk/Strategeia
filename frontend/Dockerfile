FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
# Must match the backend's API_SHARED_SECRET (see backend/.env / config.py's
# InfraSettings.api_shared_secret) or every request gets a 401 once that's
# set. Both unset (the default) is a no-op.
ARG VITE_API_SHARED_SECRET=""
ENV VITE_API_SHARED_SECRET=$VITE_API_SHARED_SECRET
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json tsconfig.json index.html ./
COPY src ./src
ARG VITE_API_URL=http://localhost:8088
ENV VITE_API_URL=$VITE_API_URL
RUN npm ci && npm run build


FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80

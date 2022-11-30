
redis-cli :
	docker exec -it subredmonitor_redis_1 redis-cli

rebuild :
	docker-compose build --no-cache
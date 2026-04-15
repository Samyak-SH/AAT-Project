.PHONY: up down logs rebuild ps train collect clean

# One command to rule them all: build + run backend + frontend.
up:
	docker compose up --build -d
	@echo ""
	@echo "Frontend:  http://localhost:5173"
	@echo "Backend:   http://localhost:8000/api/health"

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

rebuild:
	docker compose up --build --force-recreate -d

ps:
	docker compose ps

# Train the model inside a throwaway container using the mounted ml/ dir.
train:
	docker run --rm -v "$$PWD/ml:/ml" -v "$$PWD/backend:/backend" \
	    -w /ml python:3.11-slim bash -lc \
	    "pip install --quiet pandas numpy tensorflow && \
	     python train_model.py --csv sample_dataset.csv --out /backend/model.h5"
	@echo "Model written to backend/model.h5. Restart the backend:"
	@echo "  docker compose restart backend"

# Start the data-collection server (runs in foreground). LABEL=curl make collect
collect:
	@test -n "$(LABEL)" || (echo "Usage: LABEL=curl make collect"; exit 1)
	docker run --rm -it -p 8000:8000 -v "$$PWD/ml:/ml" -w /ml \
	    python:3.11-slim bash -lc \
	    "pip install --quiet fastapi uvicorn pydantic && \
	     python collect_data.py --label $(LABEL) --out sample_dataset.csv"

clean:
	docker compose down -v
	rm -f backend/sessions.db

from locust import HttpUser, task, constant
from locust.env import Environment
from locust.log import setup_logging
from locust.stats import stats_printer
import gevent
import os
import csv
from dotenv import load_dotenv
from rich.prompt import Prompt, Confirm
from rich.console import Console
from rich.panel import Panel

setup_logging("INFO", None)
console = Console()

def main():
    # Load environment variables
    load_dotenv()
    base_url = os.getenv("BASE_URL")
    
    # Show welcome message
    console.print(Panel.fit("Locust Load Test Configuration", style="bold cyan"))
    
    # Get base URL if not in .env
    if not base_url:
        base_url = Prompt.ask("Enter base URL (e.g. https://url.dev)")
        os.environ["BASE_URL"] = base_url

    # Configuration prompts
    mode = Prompt.ask(
        "Select request rate mode",
        choices=["fixed", "csv"],
        default="fixed"
    )

    get_ratio = int(Prompt.ask("GET request percentage (0-100)", default="80"))
    post_ratio = 100 - get_ratio

    if mode == "fixed":
        rps = float(Prompt.ask("Requests per second (N)"))
    else:
        try:
            with open('fifa_scaled.csv') as f:
                inter_arrival_times = [float(row[0]) for row in csv.reader(f)]
            console.print(f"Loaded {len(inter_arrival_times)} inter-arrival times")
        except FileNotFoundError:
            console.print("[red]Error: inter-arrival CSV file not found![/red]")
            return

    # Define user class dynamically
    class ApiUser(HttpUser):
        host = base_url

        if mode == "fixed":
            wait_time = constant(1 / rps)
        else:
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.inter_arrival_times = inter_arrival_times.copy()
                self.wait_index = 0

            def wait_time(self):
                if self.wait_index < len(self.inter_arrival_times):
                    wait = self.inter_arrival_times[self.wait_index]
                    self.wait_index += 1
                    return wait
                return 0  # Continue with no wait after CSV exhausted

        @task(get_ratio)
        def get_request(self):
            self.client.get("/get")

        @task(post_ratio)
        def post_request(self):
            self.client.post("/post")

    # Setup Locust environment
    env = Environment(user_classes=[ApiUser], host=base_url)
    runner = env.create_local_runner()

    # Start statistics printer
    gevent.spawn(stats_printer(env.stats))

    # Start test
    users = 1 if mode == "csv" else int(Prompt.ask("Number of concurrent users", default="1"))
    runner.start(users, spawn_rate=users)

    try:
        runner.greenlet.join()
    except KeyboardInterrupt:
        console.print("\n[bold red]Test stopped by user[/bold red]")
    finally:
        runner.quit()

if __name__ == "__main__":
    main()
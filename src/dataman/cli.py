from pathlib import Path

import click


@click.group()
def cli():
    """DataMan: Headless Data Layer API Generator."""
    pass


@cli.command()
def init():
    """Initialize a new DataMan project in the current directory."""
    cwd = Path.cwd()

    # Create .env.example file
    env_path = cwd / ".env.example"
    if not env_path.exists():
        with open(env_path, "w") as f:
            f.write("DEBUG=True\n")
            f.write("DATAMAN_SECRET_KEY=insecure-default-key-please-change\n")
            f.write("DATABASE_URL=sqlite:///db.sqlite3\n")
        click.echo(click.style("Created .env.example file.", fg="green"))
    else:
        click.echo(
            click.style(".env.example file already exists, skipping.", fg="yellow")
        )

    # Create tables directory
    tables_dir = cwd / "tables"
    if not tables_dir.exists():
        tables_dir.mkdir()
        click.echo(click.style("Created tables/ directory.", fg="green"))

        # Create an __init__.py inside it to make it a package
        init_file = tables_dir / "__init__.py"
        init_file.touch()
    else:
        click.echo(
            click.style("tables/ directory already exists, skipping.", fg="yellow")
        )

    click.echo(
        click.style("DataMan project initialized successfully!", fg="green", bold=True)
    )


if __name__ == "__main__":
    cli()

from fulcra_api.core import FulcraAPI


def main() -> None:
    client = FulcraAPI()
    client.authorize()

    print("Authorization succeeded.")
    print("Copy these values into Vercel if present:")

    values = {
        "FULCRA_ACCESS_TOKEN": client.get_cached_access_token(),
        "FULCRA_REFRESH_TOKEN": client.get_cached_refresh_token(),
        "FULCRA_ACCESS_TOKEN_EXPIRATION": client.get_cached_access_token_expiration(),
    }

    for name, value in values.items():
        if value is not None:
            print(f"{name}={value}")


if __name__ == "__main__":
    main()

import configparser

def setup_config():
    token = input("Please enter your Discord token: ")

    config = configparser.ConfigParser()
    config['DEFAULT'] = {'DISCORD_TOKEN': token}

    with open('config.ini', 'w') as configfile:
        config.write(configfile)

    print("Discord token saved to config.ini.")

if __name__ == "__main__":
    setup_config()

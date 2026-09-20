
<!-- Improved compatibility of back to top link: See: https://github.com/othneildrew/Best-README-Template/pull/73 -->

<a name="readme-top"></a>

<!-- PROJECT LOGO -->
<br />
<div align="center">
  <img src="https://kocity.xyz/img/icon.png" alt="Logo" width="80" />
</div>
<br />
<div align="center">
<h3 align="center">Knockout City Server - Docker</h3>

  <p align="center">
    An <b>Unofficial</b> Docker Image that allows running a Private Server of <a href="https://www.knockoutcity.com/">Knockout City</a>.
    <br />
    <br />
    <a href="https://github.com/KnockoutCity-Pirate-Radio/ServerDocker/issues">Report Bug</a>
  </p>
</div>

<!-- TABLE OF CONTENTS -->
<details>
  <summary>Table of Contents</summary>
  <ol>
    <li><a href="#prerequisite">Prerequisite</a></li>
    <li><a href="#downloading-the-game-data-files">Downloading the Game Data Files</a></li>
    <li>
      <a href="#usage">Usage</a>
      <ul>
        <li><a href="#environment-variables">Environment Variables</a></li>
      </ul>
    </li>
    <li><a href="#hosting-a-content-patch">Hosting a Content Patch</a></li>
    <li><a href="#notes-on-data-folder-location">Notes on Data Folder Location</a></li>
    <li><a href="#contributing">Contributing</a></li>
    <li><a href="#license">License</a></li>
  </ol>
</details>

---
## Prerequisite

- [Docker](https://www.docker.com) (any version that supports [compose v2](https://docs.docker.com/compose/migrate/))
- A Steam account (credentials required to download the server files)
- Sufficient disk space for the game data and persistent containers

---
## Downloading the Game Data Files

**Before running your server, you must manually download the Knockout City game server files using SteamCMD in Docker.**  
This is a required one-time step; the container does **not** automatically download the game data.

### Download Command

Run the following command in your terminal (replace `<username>` and `<password>` with your Steam credentials):

```bash
docker run --rm -it -v ./data:/data steamcmd/steamcmd:latest +@sSteamCmdForcePlatformType windows +force_install_dir /data +login <username> <password> +app_update 2915930 +quit
```
-   This will download the game server files into the `./data` folder (relative to your current directory).
    
-   If you want to store the game data in a different directory (for example, to share it between multiple servers), simply change the path.  
    For example, to use `/opt/KnockOutCity/data` as your data directory:
```bash
docker run --rm -it -v /opt/KnockOutCity/data:/data steamcmd/steamcmd:latest +@sSteamCmdForcePlatformType windows +force_install_dir /data +login <username> <password> +app_update 2915930 +quit
```
**Do not use the /root directory (`/root`) as a mount point.**  
Some issues where seen where Wine doesn't like it.
Always use a dedicated folder (e.g., `/opt/KnockOutCity/data`).
## Usage
Copy the [`compose.yaml`](https://github.com/KnockoutCity-Pirate-Radio/ServerDocker/blob/main/compose.yaml) and tweak it to your liking.

After downloading the game data and setting up your `compose.yaml`, you can start the server as follows:

```bash
docker compose up
```
### Environment Variables

For detailed information on the different parameters, please refer to the Knockout City Private Server README.

| **Name**                             | **Required** | **Description**                                                                     |
| ------------------------------------ | ------------ | ----------------------------------------------------------------------------------- |
| `KOC_BACKEND_MAX_PLAYER_CONNECTIONS` | No           | The maximum number of players that can be connected at the same time. *default*: 10 |
| `KOC_BACKEND_PORT`                   | No           | TCP port on which the online backend services listen. *default*: 23600              |
| `KOC_SERVER_MIN_PORT`                | No           | Start of UDP port range on which game servers will listen. *default*: 23600         |
| `KOC_SERVER_MAX_PORT`                | No           | End of UDP port range on which game servers will listen.*default*: 23699            |
| `KOC_SECRET`                         | No           | A password that, if specified, also needs to be passed to the KnockoutCity.exe      |
| `KOC_BACKEND_DB`                     | Yes          | PostgreSQL connection string to use a running PostgreSQL server.                    |
| `KOC_BACKEND_REDIS_DB_HOST`          | Yes          | Hostname of a running Redis Server.                                                 |
| `KOC_BACKEND_REDIS_DB_PORT`          | No           | Port of a running Redis Server.*default*: 6379                                      |
| `KOC_EXTRA_ARGS`                     | No           | Extra flags appended to the server command line, for anything this image does not map to a variable of its own. |
| `KOC_APPLY_EXE_PATCHES`              | No           | Apply the commerce patches to the server executable on boot. *default*: `true`      |
| `KOC_CONTENT_PATCH`                  | No           | Path (inside the container) or URL of the **server half** of a content patch. See below. |
| `KOC_CONTENT_PATCH_MANIFEST`         | No           | Path of the export's `.content_patch.json`, which advertises the **client half**.    |
| `KOC_CONTENT_PATCH_URL`              | No           | The public address clients download the client half from. Required when a manifest is given. |

---
## Hosting a Content Patch

A content patch is how a server runs modified content -- a custom event, a new
store, changed levels -- without anyone reinstalling the game.

It has two halves, and they must come from the same export:

- the **server half**, a zip of loose files unpacked into the server's
  `out/data`;
- the **client half**, a signed archive that every client downloads and
  installs at login, using the game's own content-update mechanism. No modding
  tools, no manual file copying, nothing for players to install.

Drop the three files a Pirate Radio Studio export produced into `./patch`:

```text
patch/
  holoween-2026-09-20-1.vzip                  the client half (signed)
  holoween-2026-09-20-1-server.zip            the server half
  holoween-2026-09-20-1.content_patch.json    what to tell clients
```

Point the server at them with a `.env` beside your `compose.yaml`:

```env
KOC_CONTENT_PATCH=/patch/holoween-2026-09-20-1-server.zip
KOC_CONTENT_PATCH_MANIFEST=/patch/holoween-2026-09-20-1.content_patch.json
KOC_CONTENT_PATCH_URL=http://your-public-address:8080/holoween-2026-09-20-1.vzip
```

Then `docker compose up`. On boot the container unpacks the server half into
`out/data`, patches the server executable, and -- once the backend has created
its tables -- registers the client half so logging-in clients fetch it.
Restarting changes nothing unless the zip's checksum changed.

The bundled `content` service serves `./patch` over HTTP so that URL resolves.
Drop the service if you host the file elsewhere; only the URL matters.

### Four things that will bite you

- **`KOC_CONTENT_PATCH_URL` must be the address your *players* reach**, not
  the one you use locally. `localhost` there tells every client to download
  from its own machine.
- **Every new build of a patch needs a new file name.** A client decides
  whether it already has a package by that name alone. Re-publish changed
  content under a name someone already installed and their game will not
  fetch it -- they will play stale content against a patched server, with no
  error to tell them so. A Studio export mints a fresh name every time; keep
  it.
- **`out/data` belongs to the container while `KOC_CONTENT_PATCH` is set**,
  and is deleted and rebuilt whenever the patch changes. Do not bind-mount
  your own folder there at the same time; the container refuses that
  combination rather than delete your files. To manage the folder yourself,
  leave `KOC_CONTENT_PATCH` unset.
- **The executable patches change commerce behaviour**, and are on by default:
  the shipped private-server build grants every accessory to every player and
  deletes most imported store offers on startup. Both are fixed here. Nothing
  else is touched -- in particular this image never enables the game's debug
  code paths. Set `KOC_APPLY_EXE_PATCHES` to anything but `true` for a stock
  executable.

---
## Notes on Data Folder Location

- You **can** use the same game data folder for multiple server containers by mounting the same host directory (e.g., `/opt/KnockOutCity/data`) into each container.
- **You cannot use the root directory** (`/root`) as your data volume; always use a dedicated folder for your game data.
- This folder had some issues with Wine.
- If you wish to use different game data for separate servers, specify a different host folder for each one in your compose file.

**Example for a different data folder per server:**

```yaml
  server1:
    volumes:
      - /opt/KnockOutCity/server1-data:/data
  server2:
    volumes:
      - /opt/KnockOutCity/server2-data:/data
```
## Contributing

Contributions are what make the open source community such an amazing place to learn, inspire, and create. Any contributions you make are **greatly appreciated**.

If you have a suggestion that would make this better, please fork the repo and create a pull request. You can also simply open an issue with the tag "enhancement".  
Don’t forget to give the project a star! Thanks again!

1. Fork the Project  
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)  
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)  
4. Push to the Branch (`git push origin feature/AmazingFeature`)  
5. Open a Pull Request  

---

# YouTube MCP Server

A Model Context Protocol (MCP) server for YouTube API integration. Search videos, manage playlists, and interact with YouTube directly through Claude Desktop or other MCP clients.

## Features

- 🔍 Search YouTube videos
- 📋 Create and manage playlists
- ➕ Add videos to playlists
- 📝 List user playlists
- 🗑️ Delete playlists

## Prerequisites

- Node.js 18 or higher
- YouTube Data API v3 access
- Google Cloud Console account

## Installation

### Option 1: Install from npm (Recommended)

```bash
npm install -g @a.ardeshir/youtube-mcp
```

### Option 2: Install from GitHub

```bash
git clone https://github.com/aardeshir/youtube-mcp.git
cd youtube-mcp
npm install
```

## Setup

### 1. Set Up YouTube API

#### Step 1: Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing one
3. Note your Project ID

#### Step 2: Enable YouTube Data API v3

1. In Google Cloud Console, go to "APIs & Services" > "Library"
2. Search for "YouTube Data API v3"
3. Click on it and press "Enable"

**Common Issue**: If you see "YouTube Data API v3 has not been used in project before or it is disabled", wait 2-3 minutes after enabling for it to propagate.

#### Step 3: Create OAuth 2.0 Credentials

1. Go to "APIs & Services" > "Credentials"
2. Click "+ CREATE CREDENTIALS" > "OAuth client ID"
3. If prompted, configure OAuth consent screen:
   - Choose "External" (or "Internal" for Workspace)
   - Fill required fields (app name, support email)
   - Add your email as a test user
   - For scopes, add: `https://www.googleapis.com/auth/youtube`
4. For Application type, select "Web application"
5. Add authorized redirect URI: `http://localhost:8080/callback`
6. Save and note your Client ID and Client Secret

#### Step 4: Get API Key (Optional - for read-only access)

1. Go to "APIs & Services" > "Credentials"
2. Click "+ CREATE CREDENTIALS" > "API key"
3. Restrict the key to YouTube Data API v3

### 2. Authenticate

Run the OAuth setup wizard:

**If installed via npm:**
```bash
youtube-mcp-setup
```

**If installed from GitHub:**
```bash
npm run auth-setup
```

This will:
1. Prompt for your Client ID and Client Secret
2. Open browser for Google authorization
3. Generate refresh token automatically
4. Create `.env` file with your credentials

### 3. Configure Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on Mac):

**If installed via npm:**
```json
{
  "mcpServers": {
    "youtube-mcp": {
      "command": "npx",
      "args": ["-y", "@a.ardeshir/youtube-mcp"],
      "env": {
        "YOUTUBE_API_KEY": "your-api-key",
        "YOUTUBE_CLIENT_ID": "your-client-id",
        "YOUTUBE_CLIENT_SECRET": "your-client-secret",
        "YOUTUBE_REFRESH_TOKEN": "your-refresh-token"
      }
    }
  }
}
```

**If installed from GitHub:**
```json
{
  "mcpServers": {
    "youtube-mcp": {
      "command": "node",
      "args": ["/path/to/youtube-mcp/index.js"],
      "env": {
        "YOUTUBE_API_KEY": "your-api-key",
        "YOUTUBE_CLIENT_ID": "your-client-id",
        "YOUTUBE_CLIENT_SECRET": "your-client-secret",
        "YOUTUBE_REFRESH_TOKEN": "your-refresh-token"
      }
    }
  }
}
```

## Usage Examples

### Search Videos
```
Search YouTube for "piano tutorials"
```

### Create Playlist
```
Create a YouTube playlist called "My Favorites" 
```

### List Playlists
```
Show my YouTube playlists
```

## Troubleshooting

### "YouTube Data API v3 has not been used in project before"
- Ensure API is enabled in Google Cloud Console
- Wait 2-3 minutes for changes to propagate
- Check you're using correct project

### "Invalid Credentials" error
- Verify OAuth consent screen is configured
- Ensure test user email is added
- Check redirect URI matches exactly: `http://localhost:8080/callback`

### MCP not connecting in Claude Desktop
- Check all environment variables are set
- Restart Claude Desktop after config changes
- Verify Node.js path is correct

### "Quota exceeded" error
- YouTube API has daily quotas
- Check [Google Cloud Console](https://console.cloud.google.com/) for usage
- Consider requesting quota increase

## Security Notes

- Never commit `.env` file to version control
- Keep your refresh token secure
- Use OAuth for production, API key only for testing
- Regularly rotate credentials

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create feature branch
3. Submit pull request

## License

MIT - See LICENSE file

## Support

For issues and questions:
- [GitHub Issues](https://github.com/aardeshir/youtube-mcp/issues)
- [MCP Documentation](https://modelcontextprotocol.io)

## Acknowledgments

Built with:
- [Model Context Protocol SDK](https://github.com/anthropics/mcp)
- [Google APIs Node.js Client](https://github.com/googleapis/google-api-nodejs-client)

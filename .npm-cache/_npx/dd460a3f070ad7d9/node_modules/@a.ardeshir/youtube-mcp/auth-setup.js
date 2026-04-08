#!/usr/bin/env node
import { google } from 'googleapis';
import http from 'http';
import url from 'url';
import open from 'open';
import readline from 'readline';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { dirname } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout
});

const question = (query) => new Promise((resolve) => rl.question(query, resolve));

console.log('\n🎥 YouTube MCP OAuth Setup\n');
console.log('This wizard will help you authenticate with YouTube API.\n');
console.log('Prerequisites:');
console.log('1. Google Cloud Project with YouTube Data API v3 enabled');
console.log('2. OAuth 2.0 credentials (Web application type)');
console.log('3. Redirect URI set to: http://localhost:8080/callback\n');

async function setup() {
  try {
    // Check for existing .env
    const envPath = path.join(__dirname, '.env');
    if (fs.existsSync(envPath)) {
      const overwrite = await question('⚠️  .env file exists. Overwrite? (y/n): ');
      if (overwrite.toLowerCase() !== 'y') {
        console.log('Setup cancelled.');
        process.exit(0);
      }
    }

    // Get credentials
    console.log('\nEnter your OAuth 2.0 credentials:');
    console.log('(Find these in Google Cloud Console > APIs & Services > Credentials)\n');
    
    const clientId = await question('Client ID: ');
    const clientSecret = await question('Client Secret: ');
    
    // Optional API key
    const useApiKey = await question('\nDo you also want to add an API key for read-only access? (y/n): ');
    let apiKey = '';
    if (useApiKey.toLowerCase() === 'y') {
      apiKey = await question('API Key: ');
    }

    const redirectUri = 'http://localhost:8080/callback';
    
    // Create OAuth2 client
    const oauth2Client = new google.auth.OAuth2(
      clientId,
      clientSecret,
      redirectUri
    );

    // Generate auth URL
    const authUrl = oauth2Client.generateAuthUrl({
      access_type: 'offline',
      scope: ['https://www.googleapis.com/auth/youtube'],
      prompt: 'consent'
    });

    console.log('\n🔗 Opening browser for authorization...');
    console.log('If browser doesn\'t open, visit this URL:');
    console.log(authUrl);

    // Start local server to receive callback
    const server = http.createServer(async (req, res) => {
      if (req.url.startsWith('/callback')) {
        const queryObject = url.parse(req.url, true).query;
        const code = queryObject.code;

        if (code) {
          try {
            // Exchange code for tokens
            const { tokens } = await oauth2Client.getToken(code);
            
            res.writeHead(200, { 'Content-Type': 'text/html' });
            res.end(`
              <html>
                <body style="font-family: -apple-system, system-ui, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);">
                  <div style="text-align: center; padding: 2rem; background: white; border-radius: 10px; box-shadow: 0 20px 40px rgba(0,0,0,0.1);">
                    <h1 style="color: #333; margin: 0 0 1rem 0;">✅ Success!</h1>
                    <p style="color: #666; margin: 0;">You can close this window and return to terminal.</p>
                  </div>
                </body>
              </html>
            `);

            // Create .env file
            let envContent = '';
            if (apiKey) {
              envContent += `YOUTUBE_API_KEY=${apiKey}\n`;
            }
            envContent += `YOUTUBE_CLIENT_ID=${clientId}\n`;
            envContent += `YOUTUBE_CLIENT_SECRET=${clientSecret}\n`;
            envContent += `YOUTUBE_REFRESH_TOKEN=${tokens.refresh_token}\n`;
            envContent += `YOUTUBE_REDIRECT_URI=${redirectUri}\n`;

            fs.writeFileSync(envPath, envContent);
            
            console.log('\n✅ Authentication successful!');
            console.log('📁 Credentials saved to .env file');
            console.log('\n🎉 Setup complete! You can now use the YouTube MCP server.');
            console.log('\nNext steps:');
            console.log('1. Add the server to your Claude Desktop config');
            console.log('2. Restart Claude Desktop');
            console.log('\nExample config entry:');
            console.log(JSON.stringify({
              "youtube-mcp": {
                "command": "node",
                "args": [path.join(__dirname, "index.js")],
                "env": {
                  "YOUTUBE_API_KEY": apiKey || "your-api-key",
                  "YOUTUBE_CLIENT_ID": clientId,
                  "YOUTUBE_CLIENT_SECRET": clientSecret,
                  "YOUTUBE_REFRESH_TOKEN": tokens.refresh_token
                }
              }
            }, null, 2));
            
            server.close();
            rl.close();
            process.exit(0);
          } catch (error) {
            res.writeHead(500);
            res.end('Error: ' + error.message);
            console.error('\n❌ Error exchanging code for tokens:', error.message);
            server.close();
            rl.close();
            process.exit(1);
          }
        } else {
          res.writeHead(400);
          res.end('No authorization code received');
        }
      }
    });

    server.listen(8080, () => {
      console.log('\n⏳ Waiting for authorization...');
      open(authUrl);
    });

  } catch (error) {
    console.error('\n❌ Setup failed:', error.message);
    rl.close();
    process.exit(1);
  }
}

setup();
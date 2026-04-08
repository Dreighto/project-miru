#!/usr/bin/env node
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { 
  CallToolRequestSchema,
  ListToolsRequestSchema
} from '@modelcontextprotocol/sdk/types.js';
import { google } from 'googleapis';
import dotenv from 'dotenv';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Load environment variables
dotenv.config({ path: join(__dirname, '.env') });

const server = new Server(
  {
    name: 'youtube-mcp',
    version: '1.0.0',
  },
  {
    capabilities: {
      tools: {},
    },
  }
);

// Initialize YouTube client
let youtube = null;

const apiKey = process.env.YOUTUBE_API_KEY;
const clientId = process.env.YOUTUBE_CLIENT_ID;
const clientSecret = process.env.YOUTUBE_CLIENT_SECRET;
const refreshToken = process.env.YOUTUBE_REFRESH_TOKEN;

if (clientId && clientSecret && refreshToken) {
  const oauth2Client = new google.auth.OAuth2(
    clientId,
    clientSecret,
    process.env.YOUTUBE_REDIRECT_URI || 'http://localhost:8080/callback'
  );
  
  oauth2Client.setCredentials({
    refresh_token: refreshToken
  });
  
  youtube = google.youtube({
    version: 'v3',
    auth: oauth2Client
  });
  console.error('YouTube MCP: Authenticated with OAuth');
} else if (apiKey) {
  youtube = google.youtube({
    version: 'v3',
    auth: apiKey
  });
  console.error('YouTube MCP: Authenticated with API key (read-only)');
} else {
  console.error('YouTube MCP: No authentication credentials found');
  console.error('Please run: npm run auth-setup');
}

// Handle tool list requests
server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: 'search_videos',
      description: 'Search for YouTube videos',
      inputSchema: {
        type: 'object',
        properties: {
          query: { type: 'string', description: 'Search query' },
          maxResults: { type: 'number', description: 'Max results (1-50)', default: 10 }
        },
        required: ['query']
      }
    },
    {
      name: 'create_playlist',
      description: 'Create a new YouTube playlist',
      inputSchema: {
        type: 'object',
        properties: {
          title: { type: 'string', description: 'Playlist title' },
          description: { type: 'string', description: 'Playlist description' },
          privacy: { 
            type: 'string', 
            enum: ['private', 'public', 'unlisted'],
            default: 'private'
          }
        },
        required: ['title']
      }
    },
    {
      name: 'add_to_playlist',
      description: 'Add a video to a playlist',
      inputSchema: {
        type: 'object',
        properties: {
          playlistId: { type: 'string', description: 'Playlist ID' },
          videoId: { type: 'string', description: 'Video ID to add' }
        },
        required: ['playlistId', 'videoId']
      }
    },
    {
      name: 'list_playlists',
      description: 'List user playlists',
      inputSchema: {
        type: 'object',
        properties: {
          maxResults: { type: 'number', description: 'Max results (1-50)', default: 25 }
        }
      }
    },
    {
      name: 'delete_playlist',
      description: 'Delete a YouTube playlist',
      inputSchema: {
        type: 'object',
        properties: {
          playlistId: { type: 'string', description: 'Playlist ID to delete' }
        },
        required: ['playlistId']
      }
    }
  ]
}));

// Handle tool execution
server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  
  if (!youtube) {
    return {
      content: [{
        type: 'text',
        text: 'Error: YouTube client not initialized. Please check your credentials.'
      }]
    };
  }
  
  try {
    switch (name) {
      case 'search_videos': {
        const { query, maxResults = 10 } = args;
        const response = await youtube.search.list({
          part: ['snippet'],
          q: query,
          maxResults: maxResults,
          type: ['video']
        });

        const videos = response.data.items.map(item => ({
          videoId: item.id.videoId,
          title: item.snippet.title,
          description: item.snippet.description,
          channel: item.snippet.channelTitle,
          publishedAt: item.snippet.publishedAt,
          thumbnail: item.snippet.thumbnails.default.url
        }));

        return {
          content: [{
            type: 'text',
            text: JSON.stringify(videos, null, 2)
          }]
        };
      }

      case 'create_playlist': {
        const { title, description = '', privacy = 'private' } = args;
        
        const response = await youtube.playlists.insert({
          part: ['snippet', 'status'],
          requestBody: {
            snippet: {
              title: title,
              description: description
            },
            status: {
              privacyStatus: privacy
            }
          }
        });

        return {
          content: [{
            type: 'text',
            text: JSON.stringify({
              playlistId: response.data.id,
              title: response.data.snippet.title,
              description: response.data.snippet.description,
              privacy: response.data.status.privacyStatus
            }, null, 2)
          }]
        };
      }

      case 'add_to_playlist': {
        const { playlistId, videoId } = args;
        
        await youtube.playlistItems.insert({
          part: ['snippet'],
          requestBody: {
            snippet: {
              playlistId: playlistId,
              resourceId: {
                kind: 'youtube#video',
                videoId: videoId
              }
            }
          }
        });

        return {
          content: [{
            type: 'text',
            text: `Video ${videoId} added to playlist ${playlistId}`
          }]
        };
      }

      case 'list_playlists': {
        const { maxResults = 25 } = args;
        
        const response = await youtube.playlists.list({
          part: ['snippet', 'contentDetails'],
          mine: true,
          maxResults: maxResults
        });

        const playlists = response.data.items.map(item => ({
          playlistId: item.id,
          title: item.snippet.title,
          description: item.snippet.description,
          itemCount: item.contentDetails.itemCount
        }));

        return {
          content: [{
            type: 'text',
            text: JSON.stringify(playlists, null, 2)
          }]
        };
      }

      case 'delete_playlist': {
        const { playlistId } = args;
        
        await youtube.playlists.delete({
          id: playlistId
        });

        return {
          content: [{
            type: 'text',
            text: `Playlist ${playlistId} deleted successfully`
          }]
        };
      }

      default:
        throw new Error(`Unknown tool: ${name}`);
    }
  } catch (error) {
    return {
      content: [{
        type: 'text',
        text: `Error: ${error.message}`
      }]
    };
  }
});

// Start the server
async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error('YouTube MCP server running');
}

main().catch(console.error);
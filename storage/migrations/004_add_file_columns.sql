-- Add file attachment columns to messages table
ALTER TABLE messages ADD COLUMN file_name TEXT;
ALTER TABLE messages ADD COLUMN file_path TEXT;
ALTER TABLE messages ADD COLUMN file_size INTEGER;
ALTER TABLE messages ADD COLUMN file_type TEXT;

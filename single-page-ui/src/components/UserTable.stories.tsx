import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { fn } from 'storybook/test';
import UserTable from './UserTable';
import { mockUsers } from '@/lib/gate-mock-data';

const meta: Meta<typeof UserTable> = {
  title: 'Gate/UserTable',
  component: UserTable,
  tags: ['autodocs'],
  args: { users: mockUsers, currentUsername: 'admin', onEdit: fn(), onDeactivate: fn(), onReactivate: fn() },
};

export default meta;
type Story = StoryObj<typeof UserTable>;

export const Default: Story = {};

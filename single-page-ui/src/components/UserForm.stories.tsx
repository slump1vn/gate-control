import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { fn } from 'storybook/test';
import UserForm from './UserForm';
import { mockUser } from '@/lib/gate-mock-data';

const meta: Meta<typeof UserForm> = {
  title: 'Gate/UserForm',
  component: UserForm,
  tags: ['autodocs'],
  args: { onSubmit: fn(async () => {}), onCancel: fn() },
  decorators: [(Story) => <div className="max-w-2xl"><Story /></div>],
};

export default meta;
type Story = StoryObj<typeof UserForm>;

export const New: Story = {};
export const Edit: Story = { args: { user: mockUser } };
export const EditSelf: Story = { args: { user: mockUser, isSelf: true } };

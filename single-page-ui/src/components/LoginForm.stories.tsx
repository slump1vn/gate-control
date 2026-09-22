import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { fn } from 'storybook/test';
import LoginForm from './LoginForm';

const meta: Meta<typeof LoginForm> = {
  title: 'Gate/LoginForm',
  component: LoginForm,
  tags: ['autodocs'],
  args: { onLogin: fn(async () => {}) },
  decorators: [(Story) => <div className="max-w-sm"><Story /></div>],
};

export default meta;
type Story = StoryObj<typeof LoginForm>;

export const Default: Story = {};
export const Failed: Story = { args: { initialError: 'Invalid username or password' } };
export const LockedOut: Story = { args: { initialError: 'Too many failed attempts. Try again in a few minutes.' } };

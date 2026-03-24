package main

type statusUI interface {
	SetStatus(text string)
	Close()
	Done() <-chan struct{}
}

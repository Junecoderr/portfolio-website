# Your first program

A program is a list of instructions. TB does them one by one, from top to bottom. In this chapter you write a program that shows text.

## Say hello

`say` shows a value. Put the value in brackets. Text goes in double quotes.

```tb
say("Hello, world!")
say("This is my first program.")
```

## Show more than one value

Give `say` more than one value. Put a comma between them. TB puts a space between the values.

```tb
say("I have", 2, "cats")
say("Two plus two is", 2 + 2)
say()
say("The empty say() above shows an empty line.")
```

## Notes for people

A line that starts with `#` is a comment. TB does not run it. Use comments to tell the reader why the code does something.

```tb
# Show a greeting for the analyst on duty.
say("Good morning, analyst!")   # a comment can also go at the end of a line
```

## Run a program on your computer

Save the code in a file that ends with `.tb`, for example `hello.tb`. Then type this in a terminal:

```text
tb hello.tb
```

Type `tb` with no file name to start TB Prompt. There you can type one line and see the result at once.

## What you learned

- `say(...)` shows values.
- Text goes in double quotes.
- `#` starts a comment.

Next, you learn about the kinds of values TB knows.

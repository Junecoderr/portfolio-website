(function_declaration
  body: (_) @function.inside) @function.around

(method_declaration
  body: (_) @function.inside) @function.around

(function_expression
  body: (_) @function.inside) @function.around

(lambda
  body: (_) @function.inside) @function.around

(class_declaration
  body: (_) @class.inside) @class.around

(parameters
  (_) @parameter.inside)

(arguments
  (_) @parameter.inside)

(comment) @comment.inside

(comment)+ @comment.around

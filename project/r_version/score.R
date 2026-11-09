# Usage: Rscript --vanilla score.R new_risks.csv output_quotes.csv
args_all<-commandArgs(FALSE);script<-grep('^--file=',args_all,value=TRUE)
root<-dirname(normalizePath(sub('^--file=','',script[1])));source(file.path(root,'pricing_engine.R'))
args<-commandArgs(TRUE);if(length(args)<1)stop('Supply an input CSV path')
model<-read_portable_model(file.path(root,'portable_model'));risks<-read.csv(args[1],stringsAsFactors=FALSE)
out<-if(length(args)>1)args[2] else 'new_quotes.csv'
write.csv(quote_portable(model,risks),out,row.names=FALSE,na='')
cat('Scored',nrow(risks),'research risks without Python.\n')
